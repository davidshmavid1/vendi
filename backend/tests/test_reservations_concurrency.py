import threading
from datetime import timedelta
from unittest import mock

import pytest
from django.db import connection
from django.utils import timezone

from accounts.models import User
from applications import services as application_services
from core.exceptions import DomainError
from layouts import services as layout_services
from layouts.models import StallOffer
from markets import services as market_services
from organizations import services as org_services
from reservations import services
from reservations.models import OCCUPYING, Reservation
from vendors import services as vendor_services

pytestmark = pytest.mark.django_db(transaction=True)
HOLD = timedelta(minutes=15)


def _user(email):
    return User.objects.create_user(email, "pw-long-enough-1", email_verified_at=timezone.now())


def _setup(vendor_count=2):
    owner = _user("o@example.com")
    org = org_services.create_organization(owner, name="Riverside").organization
    market = market_services.create_market(
        owner,
        org.pk,
        name="M",
        market_type="POPUP",
        timezone="UTC",
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
    application_services.configure_intake(
        owner,
        org.pk,
        market.pk,
        occurrence.pk,
        enabled=True,
        opens_at=None,
        closes_at=None,
        instructions="",
        questions=[],
    )
    stalls = [
        {
            "label": f"A{i}",
            "description": "",
            "x": i * 10,
            "y": 0,
            "width": 10,
            "height": 10,
            "physical_width": None,
            "physical_depth": None,
            "physical_unit": None,
        }
        for i in range(3)
    ]
    version, rows = layout_services.create_version(
        owner, org.pk, market.pk, canvas_width=100, canvas_height=50, stalls=stalls
    )
    layout_services.save_date_layout(
        owner,
        org.pk,
        market.pk,
        occurrence.pk,
        expected_revision=None,
        layout_version_id=version.pk,
        currency="USD",
        offers=[{"stall_id": s.pk, "price_minor": 100, "enabled": True} for s in rows],
    )
    layout_services.publish_date_layout(
        owner, org.pk, market.pk, occurrence.pk, expected_revision=1
    )
    vendors = []
    for i in range(vendor_count):
        vendor = _user(f"v{i}@example.com")
        business = vendor_services.create_business(
            vendor, name=f"V{i}", category="CRAFTS", contact_email=f"v{i}@x.example"
        ).business
        application = application_services.submit(
            vendor, business.pk, occurrence_id=occurrence.pk, questions_version=1, answers={}
        )
        application_services.decide(owner, org.pk, application.pk, approve=True)
        vendors.append((vendor, business))
    offers = list(StallOffer.objects.filter(occurrence=occurrence).order_by("pk"))
    return vendors, offers


def _race(*calls):
    barrier = threading.Barrier(len(calls))
    results = []

    def run(call):
        try:
            barrier.wait()
            results.append(call())
        except DomainError as exc:
            results.append(exc.code)
        except Exception as exc:  # e.g. a deadlock or IntegrityError: always a failure
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


def _hold(vendor, business, offer, key):
    return lambda: services.acquire_hold(vendor, business.pk, offer_id=offer.pk, request_key=key)


def _occupying():
    return Reservation.objects.filter(status__in=OCCUPYING)


@pytest.mark.parametrize("_", range(3))
def test_many_businesses_claim_one_stall(_):
    vendors, offers = _setup(vendor_count=4)
    results = _race(*[_hold(v, b, offers[0], f"k{i}") for i, (v, b) in enumerate(vendors)])
    assert _outcomes(results) == [
        "ok",
        "stall_unavailable",
        "stall_unavailable",
        "stall_unavailable",
    ]
    assert _occupying().filter(offer=offers[0]).count() == 1


@pytest.mark.parametrize("_", range(3))
def test_one_business_claims_several_stalls(_):
    vendors, offers = _setup(vendor_count=1)
    vendor, business = vendors[0]
    results = _race(*[_hold(vendor, business, offer, f"k{i}") for i, offer in enumerate(offers)])
    assert _outcomes(results) == ["hold_exists", "hold_exists", "ok"]
    assert _occupying().filter(vendor_business=business).count() == 1


@pytest.mark.parametrize("_", range(3))
def test_same_request_key_twice_at_once(_):
    vendors, offers = _setup(vendor_count=1)
    vendor, business = vendors[0]
    results = _race(
        _hold(vendor, business, offers[0], "same"), _hold(vendor, business, offers[0], "same")
    )
    assert {r.reservation.pk for r in results} == {Reservation.objects.get().pk}
    assert sorted(r.created for r in results) == [False, True]


@pytest.mark.parametrize("_", range(3))
def test_release_racing_a_new_claim(_):
    vendors, offers = _setup()
    (vendor_a, business_a), (vendor_b, business_b) = vendors
    held = services.acquire_hold(vendor_a, business_a.pk, offer_id=offers[0].pk, request_key="a")
    results = _race(
        lambda: services.release_hold(vendor_a, business_a.pk, held.reservation.pk),
        _hold(vendor_b, business_b, offers[0], "b"),
    )
    # Either B came first (stall still held) or after the release (B holds it).
    assert _outcomes(results) in (["ok", "ok"], ["ok", "stall_unavailable"])
    assert _occupying().filter(offer=offers[0]).count() == (
        1 if _outcomes(results) == ["ok", "ok"] else 0
    )
    assert Reservation.objects.get(pk=held.reservation.pk).status == "RELEASED"


@pytest.mark.parametrize("_", range(3))
def test_confirm_racing_release(_):
    vendors, offers = _setup(vendor_count=1)
    vendor, business = vendors[0]
    held = services.acquire_hold(vendor, business.pk, offer_id=offers[0].pk, request_key="a")
    results = _race(
        lambda: services.confirm_hold(held.reservation.pk),
        lambda: services.release_hold(vendor, business.pk, held.reservation.pk),
    )
    final = Reservation.objects.get().status
    if final == "CONFIRMED":
        assert _outcomes(results) == ["ok", "reservation_confirmed"]
    else:
        assert (final, _outcomes(results)) == ("RELEASED", ["hold_not_active", "ok"])


@pytest.mark.parametrize("_", range(3))
def test_confirm_after_expiry_racing_a_new_claim(_):
    vendors, offers = _setup()
    (vendor_a, business_a), (vendor_b, business_b) = vendors
    start = timezone.now()
    with mock.patch("django.utils.timezone.now", return_value=start):
        held = services.acquire_hold(
            vendor_a, business_a.pk, offer_id=offers[0].pk, request_key="a"
        )
    with mock.patch("django.utils.timezone.now", return_value=start + HOLD):
        results = _race(
            lambda: services.confirm_hold(held.reservation.pk),
            _hold(vendor_b, business_b, offers[0], "b"),
        )
    # A lapsed hold can never be confirmed; the stall goes to the new claim.
    assert _outcomes(results) == ["hold_expired", "ok"]
    assert Reservation.objects.get(pk=held.reservation.pk).status == "EXPIRED"
    assert _occupying().get().vendor_business == business_b


@pytest.mark.parametrize("_", range(3))
def test_confirm_racing_housekeeping_before_expiry(_):
    vendors, offers = _setup(vendor_count=1)
    vendor, business = vendors[0]
    held = services.acquire_hold(vendor, business.pk, offer_id=offers[0].pk, request_key="a")
    results = _race(lambda: services.confirm_hold(held.reservation.pk), services.expire_holds)
    assert Reservation.objects.get().status == "CONFIRMED"
    assert 0 in results  # housekeeping expired nothing
