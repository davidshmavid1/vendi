"""Stall payments: checkout, verified confirmation, compensation, recovery.

Permissions:

    action                                        who
    start / resume / cancel checkout              vendor business OWNER
    confirm a free stall                          vendor business OWNER
    read payment status and bookings              vendor business OWNER, MEMBER
    webhooks, reconciliation, refunds             trusted server code only

A browser redirect is never proof of payment. A payment counts only when
Stripe, asked directly (``gateway.retrieve_session`` and
``retrieve_payment_intent``), reports the Checkout Session complete and paid
and its PaymentIntent succeeded for exactly the attempt's amount, currency,
destination account and fee. Webhooks and ``reconcile`` both only trigger
that check (``sync_attempt``); neither trusts event payloads or metadata.

Locks and network calls. Stripe is never called while a database lock is
held. Each operation is: a short transaction that records intent (e.g. a
CREATING attempt with its idempotency key), the provider call, then a short
transaction that records the outcome. Every transaction that touches a
reservation locks in the order date -> reservation -> attempt -> refund
(``reservations.services.lock_reservation`` first), the same order stall
holds use.

Unknown outcomes. A provider call that times out leaves the attempt as it
was (CREATING or OPEN) with ``last_error`` set, and the reservation keeps
``payment_pending`` so its stall can't be given to someone else. Repeating
the call with the same idempotency key (a retry, or ``reconcile``) returns
the original result, so nothing is created or charged twice.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import User
from bookings.models import Booking
from core.exceptions import Conflict, InvalidRequest, NotFound, PermissionDenied, ServiceUnavailable
from moderation.policy import ParticipationRestricted, ensure_can_participate
from payments.gateway import (
    IntentSnapshot,
    ProviderError,
    RefundSnapshot,
    SessionSnapshot,
    build_gateway,
    payments_unavailable,
)
from payments.models import (
    LIVE_ATTEMPT,
    UNRESOLVED_ATTEMPT,
    UNRESOLVED_REFUND,
    AttemptStatus,
    Fulfillment,
    PaymentAccount,
    PaymentAttempt,
    Refund,
    RefundReason,
    RefundStatus,
    StripeEvent,
)
from reservations import services as reservations
from reservations.models import Reservation, ReservationStatus
from vendors.permissions import membership_for, require_owner

logger = logging.getLogger("vendi.payments")

# A CREATING attempt whose outcome is still unknown this long after its
# session's expiry is closed as FAILED. Any session Stripe did create for it
# has expired by then, and its URL never reached the vendor, so nobody can
# pay it. Without this, a persistent error (missing or revoked key) would
# keep the stall pending forever.
ABANDON_CREATING_AFTER = timedelta(minutes=10)

# Tags Vendi's stall sessions in the Stripe Dashboard. A constant, so a
# retried create sends identical parameters.
CHECKOUT_INTEGRATION_ID = "vendi-stall-checkout-qhtwmzpk"

# PaymentIntent statuses after which a delayed payment can no longer succeed.
INTENT_FAILED = ("requires_payment_method", "canceled")

_gateway = None


def gateway():
    global _gateway
    if _gateway is None:
        _gateway = build_gateway()
    return _gateway


def set_gateway(value):
    """Replace the provider (tests). Returns the previous one."""
    global _gateway
    previous, _gateway = _gateway, value
    return previous


def _require_verified(user: User) -> None:
    if not user.is_email_verified:
        raise PermissionDenied(
            "Confirm your email address before doing this.", code="email_not_verified"
        )


def _owned_reservation(actor: User, business_id: int, reservation_id: int) -> Reservation:
    _require_verified(actor)
    require_owner(membership_for(actor, business_id))
    reservation = Reservation.objects.filter(
        pk=reservation_id, vendor_business_id=business_id
    ).first()
    if reservation is None:
        raise NotFound("Reservation not found.")
    return reservation


def _problem_error(problem: str):
    if problem == "participation_restricted":
        return ParticipationRestricted(
            "You can't take part in this organization's markets right now."
        )
    if problem == "application_not_approved":
        return PermissionDenied(
            "Your application for this date is no longer approved.", code=problem
        )
    if problem == "market_unavailable":
        return Conflict("This market isn't taking bookings right now.", code=problem)
    return Conflict("This date is cancelled or has already started.", code=problem)


def _inactive_hold_error(reservation: Reservation):
    if reservation.status == ReservationStatus.CONFIRMED:
        return Conflict("This stall is already booked.", code="already_booked")
    if reservation.status == ReservationStatus.EXPIRED:
        return Conflict("This hold has expired. Choose a stall again.", code="hold_expired")
    return Conflict("This hold is no longer active.", code="hold_not_active")


# --- Starting checkout -------------------------------------------------------------------


def start_checkout(actor: User, business_id: int, reservation_id: int) -> PaymentAttempt:
    """Start, or return the already open, Checkout Session for a held paid
    stall. Safe to repeat: a second request reuses the live attempt (and its
    idempotency key) instead of creating another payable session.

    Raises ServiceUnavailable when Stripe's answer is unknown; the attempt
    stays CREATING and repeating the request resumes it.
    """
    _owned_reservation(actor, business_id, reservation_id)
    with transaction.atomic():
        attempt = _prepare_attempt(actor, reservation_id)
    if attempt.status == AttemptStatus.CREATING:
        attempt = _create_session(attempt.pk)
        if attempt.status == AttemptStatus.FAILED:
            raise Conflict(
                "The payment provider couldn't start checkout for this stall.",
                code="checkout_failed",
            )
        if attempt.status == AttemptStatus.CREATING:
            raise ServiceUnavailable(
                "We couldn't reach the payment provider. Try again in a moment.",
                code="checkout_pending",
            )
    elif attempt.status == AttemptStatus.OPEN and attempt.session_expires_at <= timezone.now():
        # Past its expiry: find out from Stripe what happened.
        attempt = sync_attempt(attempt.pk)
    return attempt


def _prepare_attempt(actor: User, reservation_id: int) -> PaymentAttempt:
    occurrence, reservation = reservations.lock_reservation(reservation_id)
    now = timezone.now()
    live = (
        PaymentAttempt.objects.select_for_update()
        .filter(reservation=reservation, status__in=LIVE_ATTEMPT)
        .first()
    )
    if live is not None:
        return live
    if reservation.status != ReservationStatus.HELD:
        raise _inactive_hold_error(reservation)
    if reservation.price_minor == 0:
        raise Conflict(
            "This stall is free; confirm it without paying.", code="payment_not_required"
        )
    problem = reservations.participation_problem(reservation, occurrence, now)
    if problem:
        raise _problem_error(problem)
    ensure_can_participate(
        occurrence.market.organization_id,
        account=actor,
        vendor_business=reservation.vendor_business,
    )
    account = PaymentAccount.objects.filter(
        organization_id=occurrence.market.organization_id, charges_enabled=True
    ).first()
    if (
        account is None
        or not gateway().is_configured()
        or account.livemode != gateway().key_livemode()
    ):
        raise payments_unavailable()
    if (
        PaymentAttempt.objects.filter(reservation=reservation).count()
        >= settings.CHECKOUT_MAX_ATTEMPTS
    ):
        raise Conflict(
            "Too many checkout attempts for this hold. Release it and choose the stall again.",
            code="checkout_limit",
        )
    session_expires_at = now + timedelta(seconds=settings.CHECKOUT_SESSION_SECONDS)
    attempt = PaymentAttempt.objects.create(
        reservation=reservation,
        organization_id=occurrence.market.organization_id,
        vendor_business=reservation.vendor_business,
        started_by=actor,
        livemode=account.livemode,
        destination_account_id=account.stripe_account_id,
        # Vendi's fee is added on top of the stall price: the vendor pays
        # both, the organizer receives the full stall price.
        amount_minor=reservation.price_minor + account.fee_for(reservation.price_minor),
        currency=reservation.currency,
        application_fee_minor=account.fee_for(reservation.price_minor),
        fee_on_top=True,
        description=(
            f"Stall {reservation.offer.stall.label} · {occurrence.market.name} · "
            f"{occurrence.starts_at:%Y-%m-%d}"
        )[:200],
        idempotency_key=f"vendi-checkout-{uuid.uuid4().hex}",
        session_expires_at=session_expires_at,
        created_at=now,
    )
    reservations.mark_payment_pending(reservation, until=session_expires_at)
    return attempt


def _return_url(attempt: PaymentAttempt, outcome: str) -> str:
    # Built only from trusted configuration and our own ids.
    base = settings.FRONTEND_BASE_URL.rstrip("/")
    return (
        f"{base}/vendor/businesses/{attempt.vendor_business_id}/reservations/"
        f"{attempt.reservation_id}/payment?checkout={outcome}"
    )


def _line_items(attempt: PaymentAttempt) -> list[dict]:
    def item(name: str, amount: int) -> dict:
        return {
            "quantity": 1,
            "price_data": {
                "currency": attempt.currency.lower(),
                "unit_amount": amount,
                "product_data": {"name": name},
            },
        }

    if not attempt.fee_on_top:  # attempts from before the fee-on-top change
        return [item(attempt.description, attempt.amount_minor)]
    items = [item(attempt.description, attempt.stall_price_minor)]
    if attempt.application_fee_minor:
        items.append(item("Vendi service fee", attempt.application_fee_minor))
    return items


def session_params(attempt: PaymentAttempt) -> dict:
    """Checkout parameters, derived only from the attempt's stored fields so a
    retry with the same idempotency key sends identical parameters."""
    intent_data = {
        "transfer_data": {"destination": attempt.destination_account_id},
        "metadata": {"vendi_payment_attempt": str(attempt.pk)},
    }
    if attempt.application_fee_minor:
        intent_data["application_fee_amount"] = attempt.application_fee_minor
    return {
        "mode": "payment",
        # No payment_method_types: Stripe shows the methods enabled in the
        # Dashboard. Delayed ones (bank debits) complete the session unpaid
        # and settle later; sync_attempt keeps the stall held meanwhile.
        "integration_identifier": CHECKOUT_INTEGRATION_ID,
        "line_items": _line_items(attempt),
        "payment_intent_data": intent_data,
        "client_reference_id": str(attempt.pk),
        "metadata": {
            "vendi_payment_attempt": str(attempt.pk),
            "vendi_reservation": str(attempt.reservation_id),
        },
        "expires_at": int(attempt.session_expires_at.timestamp()),
        "success_url": _return_url(attempt, "returned"),
        "cancel_url": _return_url(attempt, "cancelled"),
    }


def _create_session(attempt_id: int) -> PaymentAttempt:
    """Call Stripe for a CREATING attempt (no locks held) and record what
    happened. Returns the attempt as saved."""
    attempt = PaymentAttempt.objects.get(pk=attempt_id)
    if attempt.status != AttemptStatus.CREATING:
        return attempt
    try:
        session = gateway().create_checkout_session(
            session_params(attempt), idempotency_key=attempt.idempotency_key
        )
    except ProviderError as error:
        logger.warning("checkout create attempt=%s error=%s", attempt.pk, error.code)
        if error.definitive:
            # Stripe refused this key's request, so no session exists for it.
            _close_attempt(attempt.pk, AttemptStatus.FAILED, error=error.code)
        elif timezone.now() >= attempt.session_expires_at + ABANDON_CREATING_AFTER:
            _close_attempt(attempt.pk, AttemptStatus.FAILED, error=f"abandoned_{error.code}"[:100])
        else:
            PaymentAttempt.objects.filter(pk=attempt.pk).update(
                provider_calls=attempt.provider_calls + 1,
                last_error=error.code,
                updated_at=timezone.now(),
            )
        return PaymentAttempt.objects.get(pk=attempt.pk)
    with transaction.atomic():
        attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt_id)
        if attempt.status == AttemptStatus.CREATING:
            now = timezone.now()
            attempt.status = AttemptStatus.OPEN
            attempt.checkout_session_id = session.id
            attempt.checkout_url = session.url
            attempt.opened_at = now
            attempt.last_synced_at = now
            attempt.provider_calls += 1
            attempt.last_error = ""
            attempt.save()
    if session.status != "open":
        # A retried create returned a session that has since moved on.
        return sync_attempt(attempt_id)
    return PaymentAttempt.objects.get(pk=attempt_id)


# --- Applying Stripe's state --------------------------------------------------------------


def sync_attempt(attempt_id: int) -> PaymentAttempt:
    """Bring one attempt up to date with Stripe (webhooks, reconcile and the
    vendor's "check again" all use this). Idempotent. Raises nothing for
    provider problems: those are recorded in ``last_error``."""
    attempt = PaymentAttempt.objects.get(pk=attempt_id)
    if attempt.status == AttemptStatus.CREATING:
        attempt = _create_session(attempt_id)
        if attempt.status == AttemptStatus.CREATING:
            return attempt
    if attempt.status in UNRESOLVED_ATTEMPT or (
        # A paid session reported for an attempt we'd closed: never drop it.
        attempt.status in (AttemptStatus.EXPIRED, AttemptStatus.CANCELED)
    ):
        try:
            session = gateway().retrieve_session(attempt.checkout_session_id)
            intent = None
            if session.status == "complete":
                # Paid, or unpaid while a delayed method settles or fails.
                if not session.payment_intent_id:
                    raise ProviderError("session_without_intent", definitive=False)
                intent = gateway().retrieve_payment_intent(session.payment_intent_id)
        except ProviderError as error:
            logger.warning("checkout sync attempt=%s error=%s", attempt.pk, error.code)
            PaymentAttempt.objects.filter(pk=attempt.pk).update(
                provider_calls=attempt.provider_calls + 1,
                last_error=error.code,
                updated_at=timezone.now(),
            )
            return PaymentAttempt.objects.get(pk=attempt.pk)
        _apply_session(attempt.pk, session, intent)
    attempt = PaymentAttempt.objects.get(pk=attempt_id)
    if attempt.fulfillment == Fulfillment.UNFULFILLED:
        for refund in attempt.refunds.filter(status__in=UNRESOLVED_REFUND):
            process_refund(refund.pk)
    return attempt


def verification_problem(
    attempt: PaymentAttempt, session: SessionSnapshot, intent: IntentSnapshot
) -> str | None:
    """Why this paid session doesn't prove payment for this attempt, if at all."""
    checks = [
        ("session", session.id == attempt.checkout_session_id),
        ("reference", session.client_reference_id == str(attempt.pk)),
        ("livemode", session.livemode == attempt.livemode == intent.livemode),
        ("intent", intent.id == session.payment_intent_id),
        ("amount", session.amount_total == attempt.amount_minor == intent.amount_received),
        ("currency", session.currency == attempt.currency == intent.currency),
        ("destination", intent.destination == attempt.destination_account_id),
        ("fee", (intent.application_fee_amount or 0) == attempt.application_fee_minor),
    ]
    failed = [name for name, ok in checks if not ok]
    return f"verification_failed:{','.join(failed)}" if failed else None


def _apply_session(attempt_id: int, session: SessionSnapshot, intent: IntentSnapshot | None):
    if session.status == "complete" and session.payment_status == "paid" and intent:
        if intent.status != "succeeded":
            PaymentAttempt.objects.filter(pk=attempt_id).update(
                last_error="intent_not_succeeded", updated_at=timezone.now()
            )
            return
        _record_paid(
            attempt_id,
            intent,
            verification_problem(PaymentAttempt.objects.get(pk=attempt_id), session, intent),
        )
    elif session.status == "complete" and intent and intent.status in INTENT_FAILED:
        # A delayed payment method failed (checkout.session.async_payment_failed).
        _close_attempt(attempt_id, AttemptStatus.FAILED, error="async_payment_failed")
    elif session.status == "complete" and intent:
        # A delayed payment method is still settling: the session can't be
        # paid again, and the stall stays held until Stripe decides.
        PaymentAttempt.objects.filter(pk=attempt_id, status__in=UNRESOLVED_ATTEMPT).update(
            payment_intent_id=intent.id,
            last_synced_at=timezone.now(),
            last_error="",
            updated_at=timezone.now(),
        )
    elif session.status == "expired":
        _close_attempt(attempt_id, AttemptStatus.EXPIRED)
    else:
        PaymentAttempt.objects.filter(pk=attempt_id).update(
            last_synced_at=timezone.now(), last_error="", updated_at=timezone.now()
        )


def _record_paid(attempt_id: int, intent: IntentSnapshot, problem: str | None) -> None:
    """The payment succeeded: book the stall, or, if that's no longer
    possible, record it as paid but unfulfilled and request a refund. All in
    one transaction under the date, reservation and attempt locks."""
    attempt = PaymentAttempt.objects.get(pk=attempt_id)
    with transaction.atomic():
        occurrence, reservation = reservations.lock_reservation(attempt.reservation_id)
        attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt_id)
        if attempt.status == AttemptStatus.SUCCEEDED:
            return  # already recorded (duplicate or concurrent delivery)
        now = timezone.now()
        was_unresolved = attempt.status in UNRESOLVED_ATTEMPT
        attempt.status = AttemptStatus.SUCCEEDED
        attempt.payment_intent_id = intent.id
        attempt.succeeded_at = now
        attempt.last_synced_at = now
        attempt.provider_calls += 1
        attempt.closed_at = None
        reason = problem
        if reason is None and not was_unresolved:
            reason = "attempt_closed"
        if reason is None and not (
            reservation.status == ReservationStatus.HELD and reservation.payment_pending
        ):
            reason = "inventory_lost"
        if reason is None:
            reason = reservations.participation_problem(reservation, occurrence, now)
        if reason is None:
            reservations.confirm_locked(reservation, now)
            attempt.fulfillment = Fulfillment.FULFILLED
            attempt.fulfilled_at = now
            attempt.last_error = ""
            attempt.save()
            _create_booking(reservation, attempt, now)
            logger.info("payment fulfilled attempt=%s reservation=%s", attempt.pk, reservation.pk)
            return
        attempt.fulfillment = Fulfillment.UNFULFILLED
        attempt.last_error = reason[:100]
        attempt.save()
        if reservation.payment_pending and was_unresolved:
            reservations.clear_payment_pending(reservation, now, end_hold=True)
        Refund.objects.create(
            attempt=attempt,
            reason=RefundReason.UNFULFILLED,
            amount_minor=attempt.amount_minor,
            currency=attempt.currency,
            idempotency_key=f"vendi-refund-{uuid.uuid4().hex}",
        )
        logger.error(
            "payment unfulfilled attempt=%s reservation=%s reason=%s",
            attempt.pk,
            reservation.pk,
            reason,
        )


def _close_attempt(attempt_id: int, status: str, *, error: str = "") -> None:
    """An unresolved attempt ended without payment (EXPIRED, CANCELED or
    FAILED): release the reservation's payment lock."""
    attempt = PaymentAttempt.objects.get(pk=attempt_id)
    with transaction.atomic():
        _occurrence, reservation = reservations.lock_reservation(attempt.reservation_id)
        attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt_id)
        if attempt.status not in UNRESOLVED_ATTEMPT:
            return
        now = timezone.now()
        attempt.status = status
        attempt.closed_at = now
        attempt.last_synced_at = now
        attempt.provider_calls += 1
        attempt.last_error = error
        attempt.save()
        reservations.clear_payment_pending(reservation, now)


def _create_booking(reservation: Reservation, attempt: PaymentAttempt | None, now) -> Booking:
    return Booking.objects.create(
        reservation=reservation,
        offer_id=reservation.offer_id,
        occurrence_id=reservation.occurrence_id,
        application_id=reservation.application_id,
        vendor_business_id=reservation.vendor_business_id,
        price_minor=reservation.price_minor,
        currency=reservation.currency,
        payment_required=attempt is not None,
        payment_attempt=attempt,
        created_at=now,
    )


# --- Compensating refunds -----------------------------------------------------------------


def process_refund(refund_id: int) -> Refund:
    """Create (or check on) a compensating refund at Stripe. No locks are
    held during the call; the idempotency key makes retries safe."""
    refund = Refund.objects.select_related("attempt").get(pk=refund_id)
    if refund.status not in UNRESOLVED_REFUND:
        return refund
    try:
        if refund.stripe_refund_id:
            snapshot = gateway().retrieve_refund(refund.stripe_refund_id)
        else:
            snapshot = gateway().create_refund(
                payment_intent_id=refund.attempt.payment_intent_id,
                amount=refund.amount_minor,
                idempotency_key=refund.idempotency_key,
            )
    except ProviderError as error:
        logger.warning("refund refund=%s error=%s", refund.pk, error.code)
        Refund.objects.filter(pk=refund.pk).update(
            provider_calls=refund.provider_calls + 1,
            last_error=error.code,
            # Stripe refused outright: an operator must look at it.
            status=RefundStatus.FAILED if error.definitive else refund.status,
            updated_at=timezone.now(),
        )
        return Refund.objects.get(pk=refund.pk)
    return _record_refund(refund.pk, snapshot)


_REFUND_STATES = {
    "succeeded": RefundStatus.SUCCEEDED,
    "failed": RefundStatus.FAILED,
    "canceled": RefundStatus.CANCELED,
}


def _record_refund(refund_id: int, snapshot: RefundSnapshot) -> Refund:
    with transaction.atomic():
        refund = Refund.objects.select_for_update().get(pk=refund_id)
        if refund.status not in UNRESOLVED_REFUND:
            return refund  # final states never change
        refund.stripe_refund_id = snapshot.id
        refund.provider_calls += 1
        refund.last_error = ""
        refund.status = _REFUND_STATES.get(snapshot.status, RefundStatus.PENDING)
        if refund.status == RefundStatus.SUCCEEDED:
            refund.completed_at = timezone.now()
        elif refund.status != RefundStatus.PENDING:
            refund.last_error = f"refund_{snapshot.status}"
            logger.error("refund refund=%s ended %s", refund.pk, snapshot.status)
        refund.save()
    return refund


# --- Vendor operations ---------------------------------------------------------------------


def cancel_checkout(actor: User, business_id: int, reservation_id: int) -> Reservation:
    """Abandon checkout and release the stall. The session is expired at
    Stripe first; only once Stripe confirms it can no longer be paid is the
    hold released. If it was paid meanwhile, the booking stands instead."""
    _owned_reservation(actor, business_id, reservation_id)
    attempt = PaymentAttempt.objects.filter(
        reservation_id=reservation_id, status__in=UNRESOLVED_ATTEMPT
    ).first()
    if attempt is not None:
        if attempt.status == AttemptStatus.CREATING:
            attempt = sync_attempt(attempt.pk)
            if attempt.status == AttemptStatus.CREATING:
                raise ServiceUnavailable(
                    "We couldn't reach the payment provider. Try again in a moment.",
                    code="checkout_pending",
                )
        if attempt.status == AttemptStatus.OPEN:
            try:
                session = gateway().expire_session(attempt.checkout_session_id)
            except ProviderError as error:
                if not error.definitive:
                    raise ServiceUnavailable(
                        "We couldn't reach the payment provider. Try again in a moment.",
                        code="checkout_pending",
                    ) from error
                # Already complete or expired: find out which.
                attempt = sync_attempt(attempt.pk)
            else:
                if session.status == "expired":
                    _close_attempt(attempt.pk, AttemptStatus.CANCELED)
                else:
                    attempt = sync_attempt(attempt.pk)
    reservation = Reservation.objects.get(pk=reservation_id)
    if reservation.status == ReservationStatus.HELD and not reservation.payment_pending:
        reservations.release_hold(actor, business_id, reservation_id)
    elif reservation.payment_pending:
        raise ServiceUnavailable(
            "The payment's outcome isn't known yet. Try again in a moment.",
            code="checkout_pending",
        )
    return Reservation.objects.get(pk=reservation_id)


def confirm_free(actor: User, business_id: int, reservation_id: int) -> Booking:
    """Book a free (price 0) held stall without Stripe. Idempotent: returns
    the existing booking if there is one."""
    _owned_reservation(actor, business_id, reservation_id)
    with transaction.atomic():
        occurrence, reservation = reservations.lock_reservation(reservation_id)
        existing = Booking.objects.filter(reservation=reservation).first()
        if existing is not None:
            return existing
        if reservation.price_minor != 0:
            raise Conflict("This stall needs to be paid for.", code="payment_required")
        if reservation.status != ReservationStatus.HELD:
            raise _inactive_hold_error(reservation)
        now = timezone.now()
        problem = reservations.participation_problem(reservation, occurrence, now)
        if problem:
            raise _problem_error(problem)
        ensure_can_participate(
            occurrence.market.organization_id,
            account=actor,
            vendor_business=reservation.vendor_business,
        )
        reservations.confirm_locked(reservation, now)
        return _create_booking(reservation, None, now)


def check_payment(actor: User, business_id: int, reservation_id: int) -> None:
    """Ask Stripe about this reservation's open payment now (the "check
    again" button). Recovery never depends on it: webhooks and reconcile do
    the same."""
    membership_for(actor, business_id)
    attempt = (
        PaymentAttempt.objects.filter(
            reservation_id=reservation_id,
            vendor_business_id=business_id,
            status__in=UNRESOLVED_ATTEMPT,
        )
        .order_by("-pk")
        .first()
    )
    if attempt is not None:
        sync_attempt(attempt.pk)


@dataclass
class PaymentState:
    reservation: Reservation
    attempt: PaymentAttempt | None
    booking: Booking | None
    refund: Refund | None


def payment_state(actor: User, business_id: int, reservation_id: int) -> PaymentState:
    """What the vendor sees for a reservation (any member may read)."""
    membership_for(actor, business_id)
    reservation = (
        Reservation.objects.select_related("offer__stall", "occurrence__market")
        .filter(pk=reservation_id, vendor_business_id=business_id)
        .first()
    )
    if reservation is None:
        raise NotFound("Reservation not found.")
    attempt = reservation.payment_attempts.order_by("-pk").first()
    refund = attempt.refunds.order_by("-pk").first() if attempt else None
    booking = Booking.objects.filter(reservation=reservation).first()
    return PaymentState(reservation, attempt, booking, refund)


def checkout_quote(reservation: Reservation) -> dict | None:
    """The amounts checkout would charge now (for display before paying).
    The attempt created at checkout records the authoritative amounts."""
    if reservation.price_minor == 0:
        return None
    account = PaymentAccount.objects.filter(
        organization_id=reservation.occurrence.market.organization_id, charges_enabled=True
    ).first()
    if account is None:
        return None
    fee = account.fee_for(reservation.price_minor)
    return {
        "stall_price_minor": reservation.price_minor,
        "fee_minor": fee,
        "total_minor": reservation.price_minor + fee,
        "currency": reservation.currency,
    }


# --- Webhooks ------------------------------------------------------------------------------

CHECKOUT_EVENTS = {
    "checkout.session.completed",
    "checkout.session.expired",
    "checkout.session.async_payment_succeeded",
    "checkout.session.async_payment_failed",
}
REFUND_EVENTS = {"refund.created", "refund.updated", "refund.failed"}


def receive_webhook(payload: bytes, signature: str | None) -> StripeEvent:
    """Verify, record and process one delivery. Raises InvalidRequest for a
    bad signature, ServiceUnavailable when processing must be retried."""
    try:
        event = gateway().verify_webhook(payload, signature)
    except ProviderError as error:
        raise InvalidRequest("Invalid webhook signature.", code="invalid_signature") from error
    row, _created = StripeEvent.objects.get_or_create(
        event_id=event.id,
        defaults={
            "type": event.type[:100],
            "livemode": event.livemode,
            "account": event.account or "",
            "object_id": (event.object_id or "")[:255],
            "stripe_created_at": event.created,
        },
    )
    if row.processed_at is None:
        process_event(row.pk)
    return StripeEvent.objects.get(pk=row.pk)


def process_event(event_pk: int) -> None:
    """Act on a recorded event. It's marked processed only after its effects
    have committed; a provider failure leaves it unprocessed for retry."""
    row = StripeEvent.objects.get(pk=event_pk)
    if row.processed_at is not None:
        return
    note = ""
    if row.account:
        note = "ignored_connected_account"  # destination charges live on the platform
    elif row.livemode != gateway().key_livemode():
        note = "ignored_other_mode"
    elif not row.object_id:
        note = "ignored_no_object"
    elif row.type in CHECKOUT_EVENTS:
        attempt = PaymentAttempt.objects.filter(
            livemode=row.livemode, checkout_session_id=row.object_id
        ).first()
        if attempt is None:
            note = "ignored_unknown_session"  # not ours, or still CREATING (reconcile)
        else:
            attempt = sync_attempt(attempt.pk)
            if attempt.status in UNRESOLVED_ATTEMPT and attempt.last_error:
                _event_failed(row, attempt.last_error)
    elif row.type in REFUND_EVENTS:
        refund = Refund.objects.filter(stripe_refund_id=row.object_id).first()
        if refund is None:
            note = "ignored_unknown_refund"
        else:
            refund = process_refund(refund.pk)
            if refund.status in UNRESOLVED_REFUND and refund.last_error:
                _event_failed(row, refund.last_error)
    else:
        note = "ignored_type"
    StripeEvent.objects.filter(pk=row.pk, processed_at__isnull=True).update(
        processed_at=timezone.now(), attempts=row.attempts + 1, last_error=note
    )


def _event_failed(row: StripeEvent, code: str):
    StripeEvent.objects.filter(pk=row.pk).update(attempts=row.attempts + 1, last_error=code)
    raise ServiceUnavailable("Event processing will be retried.", code="event_retry")


# --- Connected accounts --------------------------------------------------------------------

# How often reconcile re-checks a linked account with Stripe. The platform
# webhook ignores connected-account events, so this is how a restricted
# account stops being offered for checkout.
ACCOUNT_RECHECK_AFTER = timedelta(hours=1)


def refresh_account(account_pk: int) -> PaymentAccount:
    """Re-read a linked connected account from Stripe. Checkout is offered
    only while its ``transfers`` capability is active (destination charges
    transfer funds to it). Raises ProviderError when Stripe can't be reached."""
    account = PaymentAccount.objects.get(pk=account_pk)
    try:
        snapshot = gateway().retrieve_account(account.stripe_account_id)
        ready = snapshot.transfers_active
    except ProviderError as error:
        if not error.definitive:
            raise
        # The account is gone or the platform lost access to it.
        logger.error("payment account=%s check refused: %s", account.pk, error.code)
        ready = False
    if account.charges_enabled != ready:
        logger.warning("payment account=%s ready changed to %s", account.pk, ready)
    PaymentAccount.objects.filter(pk=account.pk).update(
        charges_enabled=ready, verified_at=timezone.now(), updated_at=timezone.now()
    )
    return PaymentAccount.objects.get(pk=account.pk)


# --- Reconciliation ------------------------------------------------------------------------


def reconcile(limit: int = 100) -> dict:
    """Recover everything a lost webhook or an interrupted request could
    leave behind. Idempotent and bounded (``limit`` per category)."""
    now = timezone.now()
    counts = {
        "events": 0,
        "attempts": 0,
        "refunds": 0,
        "accounts": 0,
        "errors": 0,
        "expired_holds": 0,
    }

    def attempt_step(fn, key):
        try:
            fn()
            counts[key] += 1
        except Exception as error:  # keep going; the next run retries
            counts["errors"] += 1
            logger.warning(
                "reconcile %s failed: %s", key, getattr(error, "code", type(error).__name__)
            )

    stale_events = StripeEvent.objects.filter(
        processed_at__isnull=True, received_at__lte=now - timedelta(minutes=1)
    ).order_by("pk")[:limit]
    for row in stale_events:
        attempt_step(lambda row=row: process_event(row.pk), "events")

    due = PaymentAttempt.objects.filter(
        Q(status=AttemptStatus.CREATING, created_at__lte=now - timedelta(seconds=30))
        | Q(status=AttemptStatus.OPEN, session_expires_at__lte=now)
        | Q(status=AttemptStatus.OPEN, last_synced_at__lte=now - timedelta(minutes=5))
    ).order_by("pk")[:limit]
    for attempt in due:
        attempt_step(lambda a=attempt: sync_attempt(a.pk), "attempts")

    for refund in Refund.objects.filter(status__in=UNRESOLVED_REFUND).order_by("pk")[:limit]:
        attempt_step(lambda r=refund: process_refund(r.pk), "refunds")

    if gateway().is_configured():
        stale_accounts = PaymentAccount.objects.filter(
            livemode=gateway().key_livemode(),
            verified_at__lte=now - ACCOUNT_RECHECK_AFTER,
        ).order_by("verified_at")[:limit]
        for account in stale_accounts:
            attempt_step(lambda a=account: refresh_account(a.pk), "accounts")

    counts["expired_holds"] = reservations.expire_holds()
    counts["needs_operator"] = Refund.objects.filter(
        status__in=(RefundStatus.FAILED, RefundStatus.CANCELED)
    ).count()
    return counts
