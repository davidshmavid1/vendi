import threading
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from accounts.models import User
from applications import services
from applications.models import Application, ApplicationEvent
from core.exceptions import DomainError
from markets import services as market_services
from organizations import services as org_services
from vendors import services as vendor_services

pytestmark = pytest.mark.django_db(transaction=True)
QUESTIONS = [{"id": "products", "type": "short_text", "label": "What?", "required": True}]


def _user(email):
    return User.objects.create_user(email, "pw-long-enough-1", email_verified_at=timezone.now())


def _setup():
    owner, vendor = _user("o@example.com"), _user("v@example.com")
    org = org_services.create_organization(owner, name="Riverside").organization
    market = market_services.create_market(
        owner,
        org.pk,
        name="M",
        market_type="POPUP",
        timezone="America/Chicago",
        venue_name="Park",
        address_line1="1 Main",
        city="Springfield",
        country="US",
    )
    start = timezone.now() + timedelta(days=5)
    occurrence = market_services.create_occurrence(
        owner, org.pk, market.pk, starts_at=start, ends_at=start + timedelta(hours=4)
    )
    market_services.publish_market(owner, org.pk, market.pk)
    services.configure_intake(
        owner,
        org.pk,
        market.pk,
        occurrence.pk,
        enabled=True,
        opens_at=None,
        closes_at=None,
        instructions="",
        questions=QUESTIONS,
    )
    business = vendor_services.create_business(
        vendor, name="Bees", category="PRODUCE", contact_email="b@example.com"
    ).business
    return owner, vendor, org, occurrence, business


def _race(*calls):
    barrier = threading.Barrier(len(calls))
    results = []

    def run(call):
        try:
            barrier.wait()
            results.append(call())
        except DomainError as exc:
            results.append(exc.code)
        except Exception as exc:  # e.g. a deadlock: always a failure
            results.append(f"unexpected: {exc!r}")
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(call,)) for call in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not [r for r in results if isinstance(r, str) and r.startswith("unexpected")], results
    return results


@pytest.mark.parametrize("_", range(3))
def test_simultaneous_submissions_create_one_application(_):
    _owner, vendor, _org, occurrence, business = _setup()

    def submit():
        return services.submit(
            vendor,
            business.pk,
            occurrence_id=occurrence.pk,
            questions_version=1,
            answers={"products": "Honey"},
        )

    results = _race(submit, submit, submit)
    assert sorted(r if isinstance(r, str) else "created" for r in results) == [
        "application_exists",
        "application_exists",
        "created",
    ]
    assert Application.objects.count() == 1
    assert ApplicationEvent.objects.count() == 1


@pytest.mark.parametrize("_", range(3))
def test_competing_transitions_have_one_winner(_):
    owner, vendor, org, occurrence, business = _setup()
    application = services.submit(
        vendor,
        business.pk,
        occurrence_id=occurrence.pk,
        questions_version=1,
        answers={"products": "Honey"},
    )
    results = _race(
        lambda: services.decide(owner, org.pk, application.pk, approve=True),
        lambda: services.decide(owner, org.pk, application.pk, approve=False),
        lambda: services.withdraw(vendor, business.pk, application.pk),
    )
    winners = [r for r in results if not isinstance(r, str)]
    assert len(winners) == 1
    assert sorted(r for r in results if isinstance(r, str)) == [
        "application_not_submitted",
        "application_not_submitted",
    ]
    application.refresh_from_db()
    assert application.status == winners[0].status
    # Submission plus exactly one transition.
    assert ApplicationEvent.objects.filter(application=application).count() == 2


@pytest.mark.parametrize("_", range(3))
def test_question_change_during_submission_is_never_mixed(_):
    owner, vendor, org, occurrence, business = _setup()
    new_questions = [{"id": "other", "type": "short_text", "label": "New", "required": True}]

    def submit():
        return services.submit(
            vendor,
            business.pk,
            occurrence_id=occurrence.pk,
            questions_version=1,
            answers={"products": "Honey"},
        )

    def reconfigure():
        return services.configure_intake(
            owner,
            org.pk,
            occurrence.market_id,
            occurrence.pk,
            enabled=True,
            opens_at=None,
            closes_at=None,
            instructions="",
            questions=new_questions,
        )

    results = _race(submit, reconfigure)
    application = Application.objects.first()
    if application is None:
        # The questions changed first; the stale submission was refused.
        assert "questions_changed" in results
    else:
        # The submission went first and kept exactly what it answered.
        assert application.questions_version == 1
        assert [q["id"] for q in application.questions] == ["products"]
