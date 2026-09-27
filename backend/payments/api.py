"""Payment and booking endpoints. Handlers only translate HTTP; rules live in
payments/services.py and bookings/services.py.

- Vendor: /api/v1/vendors/{business_id}/reservations/{id}/checkout[/cancel],
  …/confirm-free, …/payment[/check], /api/v1/vendors/{business_id}/bookings[/{id}]
- Organizer: /api/v1/organizations/{org_id}/bookings
- Stripe: /api/v1/payments/stripe/webhook (no session: authenticated by
  Stripe's signature over the raw body, so browser CSRF doesn't apply)
"""

from django.conf import settings
from django.utils import timezone
from ninja import Router

from accounts.throttles import UserThrottle
from bookings import services as bookings
from bookings.cancellations import vendor_deadline
from bookings.models import Booking, BookingStatus
from core.auth import session_auth
from core.pagination import paginate
from core.schemas import ErrorOut
from layouts.money import exponent
from payments import services
from payments.models import AttemptStatus, Fulfillment, RefundStatus
from payments.schemas import BookingOut, BookingPage, PaymentStatusOut, WebhookOut
from reservations.api import reservation_out
from reservations.models import ReservationStatus
from vendors.permissions import is_owner, membership_for

router = Router(tags=["payments"], auth=session_auth)
organizer_router = Router(tags=["payments"], auth=session_auth)
webhook_router = Router(tags=["payments"])

_ERRORS = {
    400: ErrorOut,
    401: ErrorOut,
    403: ErrorOut,
    404: ErrorOut,
    409: ErrorOut,
    503: ErrorOut,
}
_RATES = settings.PAYMENT_RATE_LIMITS


def booking_out(booking: Booking, *, organizer: bool = False) -> dict:
    occurrence = booking.occurrence
    attempt = booking.payment_attempt
    cancellation = getattr(booking, "cancellation", None)
    refund = cancellation.refund if cancellation is not None else None
    return {
        "id": booking.pk,
        "reservation_id": booking.reservation_id,
        "status": booking.status,
        "stall": {"id": booking.offer.stall_id, "label": booking.offer.stall.label},
        "occurrence": {
            "id": occurrence.pk,
            "market_id": occurrence.market_id,
            "market_name": occurrence.market.name,
            "starts_at": occurrence.starts_at,
            "ends_at": occurrence.ends_at,
            "timezone": occurrence.market.timezone,
        },
        "vendor_business": {"id": booking.vendor_business_id, "name": booking.vendor_business.name},
        "price_minor": booking.price_minor,
        "currency": booking.currency,
        "currency_exponent": exponent(booking.currency),
        "payment_required": booking.payment_required,
        "paid_minor": attempt.amount_minor if attempt else 0,
        "fee_minor": attempt.application_fee_minor if attempt else 0,
        "paid_at": attempt.succeeded_at if attempt else None,
        "created_at": booking.created_at,
        "cancelled_at": booking.cancelled_at,
        "terms": {
            "captured": booking.policy_captured_at is not None,
            "vendor_cutoff_hours": booking.policy_vendor_cutoff_hours,
            "vendor_deadline": vendor_deadline(booking, occurrence),
        },
        "cancellation": {
            "kind": cancellation.kind,
            "reason": cancellation.reason,
            "requested_at": cancellation.requested_at,
            "completed_at": cancellation.completed_at,
            "refund_rule": cancellation.refund_rule,
            "refund_entitlement_minor": cancellation.refund_entitlement_minor,
            "internal_note": cancellation.internal_note if organizer else None,
        }
        if cancellation is not None
        else None,
        "refund": {
            "status": refund.status,
            "amount_minor": refund.amount_minor,
            "currency": refund.currency,
            "completed_at": refund.completed_at,
        }
        if refund is not None
        else None,
    }


def _state(state: services.PaymentState, now) -> str:
    attempt, refund = state.attempt, state.refund
    if state.booking is not None and state.booking.status == BookingStatus.CANCELLED:
        return "CANCELLED"
    if state.booking is not None or state.reservation.status == ReservationStatus.CONFIRMED:
        return "BOOKED"
    if attempt and attempt.fulfillment == Fulfillment.UNFULFILLED:
        if refund and refund.status == RefundStatus.SUCCEEDED:
            return "REFUNDED"
        if refund and refund.status in (RefundStatus.FAILED, RefundStatus.CANCELED):
            return "REFUND_FAILED"
        return "REFUND_PENDING"
    if attempt and attempt.status == AttemptStatus.CREATING:
        return "PROCESSING"
    if attempt and attempt.status == AttemptStatus.OPEN:
        # A PaymentIntent on an open attempt means checkout finished with a
        # delayed payment method that hasn't settled yet.
        if attempt.payment_intent_id or attempt.session_expires_at <= now:
            return "PROCESSING"
        return "CHECKOUT_OPEN"
    status = state.reservation.status_at(now)
    if status == ReservationStatus.HELD:
        return "HOLDING"
    return "RELEASED" if status == ReservationStatus.RELEASED else "EXPIRED"


def _status_out(request, business_id: int, reservation_id: int) -> dict:
    state = services.payment_state(request.auth, business_id, reservation_id)
    owner = is_owner(membership_for(request.auth, business_id))
    now = timezone.now()
    name = _state(state, now)
    attempt = state.attempt
    payment = None
    if attempt is not None:
        payment = {
            "id": attempt.pk,
            "status": attempt.status,
            "fulfillment": attempt.fulfillment or None,
            "amount_minor": attempt.amount_minor,
            "stall_price_minor": attempt.stall_price_minor,
            "fee_minor": attempt.application_fee_minor,
            "currency": attempt.currency,
            "currency_exponent": exponent(attempt.currency),
            "session_expires_at": attempt.session_expires_at,
            "succeeded_at": attempt.succeeded_at,
            "checkout_url": attempt.checkout_url
            if owner and name == "CHECKOUT_OPEN" and attempt.checkout_url
            else None,
        }
    refund = None
    if state.refund is not None:
        refund = {
            "status": state.refund.status,
            "amount_minor": state.refund.amount_minor,
            "currency": state.refund.currency,
        }
    booking = (
        bookings.list_for_business(request.auth, business_id)
        .filter(reservation_id=reservation_id)
        .first()
    )
    return {
        "state": name,
        "payment_required": state.reservation.price_minor > 0,
        "quote": _quote(state.reservation) if name == "HOLDING" else None,
        "reservation": reservation_out(state.reservation),
        "payment": payment,
        "booking": booking_out(booking) if booking else None,
        "refund": refund,
    }


def _quote(reservation) -> dict | None:
    quote = services.checkout_quote(reservation)
    if quote is not None:
        quote["currency_exponent"] = exponent(quote["currency"])
    return quote


_RESERVATION = "/{business_id}/reservations/{reservation_id}"


@router.post(
    f"{_RESERVATION}/checkout",
    response={200: PaymentStatusOut, 429: ErrorOut, **_ERRORS},
    throttle=[UserThrottle("checkout_user", _RATES)],
)
def checkout(request, business_id: int, reservation_id: int):
    """Start checkout for a held stall, or return the one already open (the
    response's payment.checkout_url). Repeating it never creates a second
    payable session. 503 means the provider's answer is unknown: retry."""
    services.start_checkout(request.auth, business_id, reservation_id)
    return _status_out(request, business_id, reservation_id)


@router.post(f"{_RESERVATION}/checkout/cancel", response={200: PaymentStatusOut, **_ERRORS})
def cancel_checkout(request, business_id: int, reservation_id: int):
    """Expire the open Checkout Session at Stripe, then release the stall.
    If the payment went through first, the booking stands."""
    services.cancel_checkout(request.auth, business_id, reservation_id)
    return _status_out(request, business_id, reservation_id)


@router.post(f"{_RESERVATION}/confirm-free", response={200: PaymentStatusOut, **_ERRORS})
def confirm_free(request, business_id: int, reservation_id: int):
    """Book a free stall (price 0) without payment. Repeating it is safe."""
    services.confirm_free(request.auth, business_id, reservation_id)
    return _status_out(request, business_id, reservation_id)


@router.get(f"{_RESERVATION}/payment", response={200: PaymentStatusOut, **_ERRORS})
def payment_status(request, business_id: int, reservation_id: int):
    """The authoritative payment and booking state (the checkout return page
    reads this; URL parameters never confirm anything)."""
    return _status_out(request, business_id, reservation_id)


@router.post(
    f"{_RESERVATION}/payment/check",
    response={200: PaymentStatusOut, 429: ErrorOut, **_ERRORS},
    throttle=[UserThrottle("payment_check_user", _RATES)],
)
def check_payment(request, business_id: int, reservation_id: int):
    """Ask Stripe now instead of waiting for its webhook."""
    services.check_payment(request.auth, business_id, reservation_id)
    return _status_out(request, business_id, reservation_id)


@router.get("/{business_id}/bookings", response={200: BookingPage, **_ERRORS})
def list_bookings(
    request,
    business_id: int,
    occurrence_id: int | None = None,
    cursor: int | None = None,
    limit: int | None = None,
):
    queryset = bookings.list_for_business(request.auth, business_id, occurrence_id=occurrence_id)
    items, next_cursor = paginate(queryset, cursor=cursor, limit=limit)
    return {"items": [booking_out(b) for b in items], "next_cursor": next_cursor}


@router.get("/{business_id}/bookings/{booking_id}", response={200: BookingOut, **_ERRORS})
def get_booking(request, business_id: int, booking_id: int):
    return booking_out(bookings.get_for_business(request.auth, business_id, booking_id))


@organizer_router.get("/{organization_id}/bookings", response={200: BookingPage, **_ERRORS})
def organization_bookings(
    request,
    organization_id: int,
    market_id: int | None = None,
    occurrence_id: int | None = None,
    cursor: int | None = None,
    limit: int | None = None,
):
    """Confirmed bookings on the organization's dates (any team member)."""
    queryset = bookings.list_for_organization(
        request.auth, organization_id, market_id=market_id, occurrence_id=occurrence_id
    )
    items, next_cursor = paginate(queryset, cursor=cursor, limit=limit)
    return {"items": [booking_out(b, organizer=True) for b in items], "next_cursor": next_cursor}


@webhook_router.post(
    "/stripe/webhook", response={200: WebhookOut, 400: ErrorOut, 503: ErrorOut}, auth=None
)
def stripe_webhook(request):
    """Stripe event deliveries. Verified against the raw body with the
    endpoint's signing secret; 503 asks Stripe to retry later."""
    services.receive_webhook(request.body, request.headers.get("Stripe-Signature"))
    return {"received": True}
