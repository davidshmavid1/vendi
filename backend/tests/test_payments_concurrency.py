from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from bookings.models import Booking
from layouts.models import StallOffer
from payments import services
from payments.models import AttemptStatus, PaymentAccount, PaymentAttempt, Refund
from reservations import services as reservation_services
from reservations.models import Reservation, ReservationStatus
from tests.fake_stripe import FakeStripe
from tests.test_reservations_concurrency import _outcomes, _race, _setup

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def stripe():
    fake = FakeStripe()
    previous = services.set_gateway(fake)
    yield fake
    services.set_gateway(previous)


def _world(vendor_count=2):
    vendors, offers = _setup(vendor_count=vendor_count)
    PaymentAccount.objects.create(
        organization_id=offers[0].occurrence.market.organization_id,
        stripe_account_id="acct_test123",
        livemode=False,
        charges_enabled=True,
        verified_at=timezone.now(),
    )
    return vendors, offers


def _held(vendor, business, offer, key="k"):
    return reservation_services.acquire_hold(
        vendor, business.pk, offer_id=offer.pk, request_key=key
    ).reservation


@pytest.mark.parametrize("_", range(3))
def test_concurrent_checkout_clicks_create_one_payable_session(_, stripe):
    vendors, offers = _world(vendor_count=1)
    vendor, business = vendors[0]
    reservation = _held(vendor, business, offers[0])
    start = lambda: services.start_checkout(vendor, business.pk, reservation.pk)  # noqa: E731
    results = _race(start, start, start)
    assert {r.pk for r in results if not isinstance(r, str)} == {PaymentAttempt.objects.get().pk}
    assert len(stripe.sessions) == 1


@pytest.mark.parametrize("_", range(3))
def test_concurrent_confirmations_create_one_booking(_, stripe):
    vendors, offers = _world(vendor_count=1)
    vendor, business = vendors[0]
    reservation = _held(vendor, business, offers[0])
    attempt = services.start_checkout(vendor, business.pk, reservation.pk)
    stripe.pay(attempt.checkout_session_id)
    sync = lambda: services.sync_attempt(attempt.pk)  # noqa: E731
    _race(sync, sync, sync)
    assert Booking.objects.count() == 1
    assert Refund.objects.count() == 0
    assert Reservation.objects.get().status == ReservationStatus.CONFIRMED


@pytest.mark.parametrize("_", range(3))
def test_payment_racing_a_competitor_after_the_original_hold_time(_, stripe):
    """Past the 15-minute hold but inside checkout, a competitor never gets
    the stall, whether the payment is confirmed before or after."""
    vendors, offers = _world()
    (vendor_a, business_a), (vendor_b, business_b) = vendors
    start = timezone.now()
    with mock.patch("django.utils.timezone.now", return_value=start):
        reservation = _held(vendor_a, business_a, offers[0])
        attempt = services.start_checkout(vendor_a, business_a.pk, reservation.pk)
    stripe.pay(attempt.checkout_session_id)
    with mock.patch("django.utils.timezone.now", return_value=start + timedelta(minutes=20)):
        results = _race(
            lambda: services.sync_attempt(attempt.pk),
            lambda: reservation_services.acquire_hold(
                vendor_b, business_b.pk, offer_id=offers[0].pk, request_key="b"
            ),
            reservation_services.expire_holds,
        )
    assert "stall_unavailable" in _outcomes(results)
    assert Booking.objects.get().vendor_business == business_a


@pytest.mark.parametrize("_", range(3))
def test_cancel_racing_the_payment_never_both_releases_and_books(_, stripe):
    vendors, offers = _world(vendor_count=1)
    vendor, business = vendors[0]
    reservation = _held(vendor, business, offers[0])
    attempt = services.start_checkout(vendor, business.pk, reservation.pk)
    stripe.pay(attempt.checkout_session_id)
    _race(
        lambda: services.cancel_checkout(vendor, business.pk, reservation.pk),
        lambda: services.sync_attempt(attempt.pk),
    )
    # The session was already paid, so Stripe refuses to expire it and the
    # booking stands.
    assert Reservation.objects.get().status == ReservationStatus.CONFIRMED
    assert PaymentAttempt.objects.get().status == AttemptStatus.SUCCEEDED
    assert Booking.objects.count() == 1


@pytest.mark.parametrize("_", range(3))
def test_double_free_confirmation_creates_one_booking(_, stripe):
    vendors, offers = _world(vendor_count=1)
    vendor, business = vendors[0]
    StallOffer.objects.filter(pk=offers[0].pk).update(price_minor=0)
    reservation = _held(vendor, business, offers[0])
    confirm = lambda: services.confirm_free(vendor, business.pk, reservation.pk)  # noqa: E731
    results = _race(confirm, confirm)
    assert len({r.pk for r in results}) == 1
    assert Booking.objects.count() == 1
    assert stripe.calls == []
