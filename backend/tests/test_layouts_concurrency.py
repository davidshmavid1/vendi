import threading
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from accounts.models import User
from core.exceptions import DomainError
from layouts import services
from layouts.models import Stall, StallLayout
from markets import services as market_services
from organizations import services as org_services

pytestmark = pytest.mark.django_db(transaction=True)


def _setup():
    owner = User.objects.create_user(
        "o@example.com", "pw-long-enough-1", email_verified_at=timezone.now()
    )
    org = org_services.create_organization(owner, name="Riverside").organization
    market = market_services.create_market(
        owner, org.pk, name="M", market_type="POPUP", timezone="UTC"
    )
    start = timezone.now() + timedelta(days=5)
    occurrence = market_services.create_occurrence(
        owner, org.pk, market.pk, starts_at=start, ends_at=start + timedelta(hours=4)
    )
    return owner, org, market, occurrence


def _stall(label, x, price):
    return {
        "label": label,
        "description": "",
        "x": x,
        "y": 0,
        "width": 10,
        "height": 10,
        "physical_width": None,
        "physical_depth": None,
        "physical_unit": None,
        "enabled": True,
        "price_minor": price,
    }


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
def test_concurrent_edits_of_the_same_revision_have_one_winner(_):
    owner, org, market, occurrence = _setup()
    services.save_layout(
        owner,
        org.pk,
        market.pk,
        occurrence.pk,
        expected_revision=None,
        canvas_width=100,
        canvas_height=50,
        currency="USD",
        stalls=[],
    )

    def edit(label, price):
        return lambda: services.save_layout(
            owner,
            org.pk,
            market.pk,
            occurrence.pk,
            expected_revision=1,
            canvas_width=100,
            canvas_height=50,
            currency="USD",
            stalls=[_stall(label, 0, price)],
        )

    results = _race(edit("A1", 100), edit("B1", 200))
    assert sorted(r if isinstance(r, str) else "saved" for r in results) == [
        "saved",
        "stale_revision",
    ]
    layout = StallLayout.objects.get()
    assert layout.revision == 2
    # Exactly one editor's stalls, never a mix.
    assert Stall.objects.count() == 1


@pytest.mark.parametrize("_", range(3))
def test_concurrent_creation_makes_one_layout(_):
    owner, org, market, occurrence = _setup()

    def create(label):
        return lambda: services.save_layout(
            owner,
            org.pk,
            market.pk,
            occurrence.pk,
            expected_revision=None,
            canvas_width=100,
            canvas_height=50,
            currency="USD",
            stalls=[_stall(label, 0, 1)],
        )

    results = _race(create("A1"), create("B1"))
    assert sorted(r if isinstance(r, str) else "saved" for r in results) == [
        "saved",
        "stale_revision",
    ]
    assert StallLayout.objects.count() == 1
    assert Stall.objects.count() == 1
