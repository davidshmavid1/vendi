import threading
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from accounts.models import User
from core.exceptions import DomainError
from layouts import services
from layouts.models import LayoutVersion, OccurrenceLayout, Stall
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


def _stall(label, x=0, **extra):
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
        **extra,
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
    unexpected = [r for r in results if isinstance(r, str) and r.startswith("unexpected")]
    assert not unexpected, results
    return results


def _outcomes(results):
    return sorted(r if isinstance(r, str) else "ok" for r in results)


@pytest.mark.parametrize("_", range(3))
def test_concurrent_edits_of_one_version_have_one_winner(_):
    owner, org, market, _occurrence = _setup()
    version, _stalls = services.create_version(
        owner, org.pk, market.pk, canvas_width=100, canvas_height=50, stalls=[]
    )

    def edit(label):
        return lambda: services.save_version(
            owner,
            org.pk,
            market.pk,
            version.pk,
            expected_revision=1,
            canvas_width=100,
            canvas_height=50,
            stalls=[_stall(label)],
        )

    assert _outcomes(_race(edit("A1"), edit("B1"))) == ["ok", "stale_revision"]
    assert LayoutVersion.objects.get().revision == 2
    assert Stall.objects.count() == 1  # one editor's stalls, never a mix


@pytest.mark.parametrize("_", range(3))
def test_concurrent_version_creation_numbers_uniquely(_):
    owner, org, market, _occurrence = _setup()

    def create():
        return services.create_version(
            owner, org.pk, market.pk, canvas_width=100, canvas_height=50, stalls=[]
        )

    assert _outcomes(_race(create, create, create)) == ["ok", "ok", "ok"]
    assert sorted(LayoutVersion.objects.values_list("number", flat=True)) == [1, 2, 3]


@pytest.mark.parametrize("_", range(3))
def test_concurrent_date_saves_have_one_winner(_):
    owner, org, market, occurrence = _setup()
    version, stalls = services.create_version(
        owner, org.pk, market.pk, canvas_width=100, canvas_height=50, stalls=[_stall("A1")]
    )

    def assign(price):
        return lambda: services.save_date_layout(
            owner,
            org.pk,
            market.pk,
            occurrence.pk,
            expected_revision=None,
            layout_version_id=version.pk,
            currency="USD",
            offers=[{"stall_id": stalls[0].pk, "price_minor": price, "enabled": True}],
        )

    assert _outcomes(_race(assign(100), assign(200))) == ["ok", "stale_revision"]
    assert OccurrenceLayout.objects.get().revision == 1


@pytest.mark.parametrize("_", range(3))
def test_version_edit_never_lands_after_a_date_uses_it(_):
    owner, org, market, occurrence = _setup()
    version, stalls = services.create_version(
        owner, org.pk, market.pk, canvas_width=100, canvas_height=50, stalls=[_stall("A1")]
    )

    def edit():
        return services.save_version(
            owner,
            org.pk,
            market.pk,
            version.pk,
            expected_revision=1,
            canvas_width=100,
            canvas_height=50,
            stalls=[{**_stall("A1", x=50), "id": stalls[0].pk}],
        )

    def assign():
        return services.save_date_layout(
            owner,
            org.pk,
            market.pk,
            occurrence.pk,
            expected_revision=None,
            layout_version_id=version.pk,
            currency="USD",
            offers=[{"stall_id": stalls[0].pk, "price_minor": 100, "enabled": True}],
        )

    results = _race(edit, assign)
    version.refresh_from_db()
    assert version.locked_at is not None
    if "layout_version_locked" in results:
        # The date got the plan first; the edit was refused.
        assert Stall.objects.get().x == 0
    else:
        # The edit finished before the date took the (edited) plan.
        assert _outcomes(results) == ["ok", "ok"]
        assert (version.revision, Stall.objects.get().x) == (2, 50)
