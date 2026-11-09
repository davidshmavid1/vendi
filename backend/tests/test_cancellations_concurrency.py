import pytest
from django.db.models import Sum

from accounts.models import User
from bookings import cancellations
from bookings.models import Booking, BookingCancellation
from markets import services as market_services
from payments import services as payments
from payments.models import AttemptStatus, PaymentAccount, PaymentAttempt, Refund
from reservations import services as reservation_services
from reservations.models import OCCUPYING, Reservation
from tests.fake_stripe import FakeStripe
from tests.test_reservations_concurrency import _race, _setup

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def stripe():
    fake = FakeStripe()
    previous = payments.set_gateway(fake)
    yield fake
    payments.set_gateway(previous)


def _world(vendor_count=2):
    vendors, offers = _setup(vendor_count=vendor_count)
    market = offers[0].occurrence.market
    market.vendor_cancellation_cutoff_hours = 24
    market.save(update_fields=["vendor_cancellation_cutoff_hours"])
    PaymentAccount.objects.create(
        organization_id=market.organization_id,
        stripe_account_id="acct_test123",
        livemode=False,
        charges_enabled=True,
        verified_at=offers[0].occurrence.starts_at,
    )
    return vendors, offers, User.objects.get(email="o@example.com")


def _booking(stripe, vendor, business, offer, key="k") -> Booking:
    reservation = reservation_services.acquire_hold(
        vendor, business.pk, offer_id=offer.pk, request_key=key
    ).reservation
    attempt = payments.start_checkout(vendor, business.pk, reservation.pk)
    stripe.pay(attempt.checkout_session_id)
    payments.sync_attempt(attempt.pk)
    return Booking.objects.get(reservation=reservation)


def _vendor_cancel(vendor, business, booking):
    return lambda: cancellations.vendor_cancel(
        vendor, business.pk, booking.pk, expected_refund_minor=100, currency="USD"
    )


def _cancel_date(owner, occurrence):
    market = occurrence.market
    return lambda: market_services.cancel_occurrence(
        owner, market.organization_id, market.pk, occurrence.pk, message="Storm"
    )


def _refunded_total(booking) -> int:
    return (
        Refund.objects.filter(attempt=booking.payment_attempt)
        .exclude(status__in=("FAILED", "CANCELED"))
        .aggregate(total=Sum("amount_minor"))["total"]
        or 0
    )


@pytest.mark.parametrize("_", range(3))
def test_concurrent_vendor_and_organizer_cancellations_act_once(_, stripe):
    vendors, offers, owner = _world(vendor_count=1)
    vendor, business = vendors[0]
    booking = _booking(stripe, vendor, business, offers[0])
    org_id = offers[0].occurrence.market.organization_id
    organizer = lambda: cancellations.organizer_cancel(  # noqa: E731
        owner, org_id, booking.pk, expected_refund_minor=100, currency="USD", reason="x"
    )
    results = _race(
        _vendor_cancel(vendor, business, booking),
        _vendor_cancel(vendor, business, booking),
        organizer,
    )
    assert sorted(r.created for r in results) == [False, False, True]
    assert BookingCancellation.objects.count() == 1
    assert Refund.objects.filter(reason="CANCELLATION").count() == 1
    # 101 paid (100 + 1% fee): at most 100 goes back, never more than was paid.
    assert _refunded_total(booking) == 100
    assert Reservation.objects.get(pk=booking.reservation_id).status == "CANCELLED"


@pytest.mark.parametrize("_", range(3))
def test_date_cancellation_racing_checkout_creation(_, stripe):
    vendors, offers, owner = _world(vendor_count=1)
    vendor, business = vendors[0]
    reservation = reservation_services.acquire_hold(
        vendor, business.pk, offer_id=offers[0].pk, request_key="k"
    ).reservation
    results = _race(
        lambda: payments.start_checkout(vendor, business.pk, reservation.pk),
        _cancel_date(owner, offers[0].occurrence),
    )
    attempts = PaymentAttempt.objects.filter(reservation=reservation)
    if "occurrence_unavailable" in [r for r in results if isinstance(r, str)]:
        assert not attempts.exists()  # the date was cancelled first
    # Either way the cancellation settles it: no stall stays taken and
    # nothing can be booked.
    cancellations.process_occurrence_cancellations()
    assert not Reservation.objects.filter(status__in=OCCUPYING).exists()
    assert not attempts.filter(status__in=(AttemptStatus.CREATING, AttemptStatus.OPEN)).exists()
    assert not Booking.objects.exists()


@pytest.mark.parametrize("_", range(3))
def test_date_cancellation_racing_payment_success(_, stripe):
    vendors, offers, owner = _world(vendor_count=1)
    vendor, business = vendors[0]
    reservation = reservation_services.acquire_hold(
        vendor, business.pk, offer_id=offers[0].pk, request_key="k"
    ).reservation
    attempt = payments.start_checkout(vendor, business.pk, reservation.pk)
    stripe.pay(attempt.checkout_session_id)
    _race(lambda: payments.sync_attempt(attempt.pk), _cancel_date(owner, offers[0].occurrence))
    cancellations.process_occurrence_cancellations()
    attempt.refresh_from_db()
    booking = Booking.objects.filter(reservation=reservation).first()
    if booking is not None:
        # Paid first: booked, then cancelled with the date (paid minus fee back).
        assert booking.status == "CANCELLED" and booking.cancellation.kind == "EVENT"
        assert _refunded_total(booking) == 100
    else:
        # Cancelled first: the late payment is refunded in full, never booked.
        assert attempt.fulfillment == "UNFULFILLED"
        assert Refund.objects.get(attempt=attempt).amount_minor == 101
    assert not Reservation.objects.filter(status__in=OCCUPYING).exists()


@pytest.mark.parametrize("_", range(3))
def test_vendor_cancellation_racing_date_cancellation(_, stripe):
    vendors, offers, owner = _world(vendor_count=1)
    vendor, business = vendors[0]
    booking = _booking(stripe, vendor, business, offers[0])
    _race(_vendor_cancel(vendor, business, booking), _cancel_date(owner, offers[0].occurrence))
    cancellations.process_occurrence_cancellations()
    cancellation = BookingCancellation.objects.get()
    assert cancellation.kind in ("VENDOR", "EVENT")
    assert Refund.objects.filter(reason="CANCELLATION").count() == 1
    assert _refunded_total(booking) == 100
