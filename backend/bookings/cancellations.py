"""Booking cancellations and refunds (Phase 14).

Policy (approved):

- Each market may set a vendor cancellation cutoff in hours
  (``Market.vendor_cancellation_cutoff_hours``); holds snapshot it and
  bookings copy it, so later edits never change existing bookings.
- A vendor OWNER may cancel strictly before ``starts_at - cutoff`` (the
  date's current start). At or after that instant, or when the market set
  no cutoff, vendors can't cancel; they contact the organizer.
- Bookings made before policies existed (no snapshot) can't be cancelled
  by vendors (manual review); organizers can still cancel them.
- Every cancellation of a paid booking refunds the amount paid minus Vendi's
  fee. The organizer's transfer is reversed for that amount; Vendi keeps its
  fee. Free bookings refund nothing. No partial refunds, fees or credits.

Permissions:

    action                                   who
    preview, read status                     vendor OWNER/MEMBER; org OWNER/ADMIN/STAFF
    vendor cancellation                      vendor OWNER
    organizer cancellation                   organization OWNER, ADMIN
    date cancellation                        organization OWNER, ADMIN (markets)

Participation restrictions never block reading or cancelling.

States are separate: the booking (CONFIRMED -> CANCELLED), its reservation
(inventory: CONFIRMED -> CANCELLED, released exactly once by
``reservations.release_for_cancellation``) and the Refund (settlement:
REQUESTED -> PENDING -> SUCCEEDED, or FAILED/CANCELED/REVIEW). A cancelled
booking's refund may still be in flight.

Locks: the date, then the reservation (``lock_reservation``), then the
booking, then the payment attempt; the same order as holds and payments.
Stripe is never called with a lock held.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from accounts.models import User
from bookings.models import (
    Booking,
    BookingCancellation,
    BookingStatus,
    CancellationItemKind,
    CancellationItemStatus,
    CancellationKind,
    OccurrenceCancellation,
    OccurrenceCancellationItem,
    RefundRule,
)
from core.exceptions import Conflict, InvalidRequest, NotFound, PermissionDenied
from markets.models import EventOccurrence, OccurrenceStatus
from organizations.models import Role
from organizations.permissions import membership_for as organization_membership
from organizations.permissions import require_role
from payments import services as payments
from payments.models import Refund
from reservations import services as reservations
from reservations.models import ReservationStatus
from vendors.permissions import is_owner
from vendors.permissions import membership_for as vendor_membership

logger = logging.getLogger("vendi.bookings")


# --- Terms and entitlement ------------------------------------------------------------------


@dataclass(frozen=True)
class Quote:
    """What cancelling this booking would do, computed by the server."""

    permitted: bool
    problem: str | None  # why not, as a stable code
    refund_rule: str
    paid_minor: int
    fee_retained_minor: int
    refund_minor: int
    currency: str
    deadline: datetime | None  # vendor cutoff instant (cancel strictly before)
    releases_stall: bool


PROBLEM_MESSAGES = {
    "already_cancelled": "This booking is already cancelled.",
    "occurrence_cancelled": "This date was cancelled; its bookings are cancelled and refunded "
    "automatically.",
    "manual_review": "This booking was made before cancellation terms existed. Contact the "
    "organizer to cancel it.",
    "vendor_cancellation_not_offered": "This market doesn't let vendors cancel online. Contact "
    "the organizer.",
    "cutoff_passed": "The cancellation deadline for this booking has passed. Contact the "
    "organizer.",
}


def vendor_deadline(booking: Booking, occurrence: EventOccurrence) -> datetime | None:
    if booking.policy_captured_at is None or booking.policy_vendor_cutoff_hours is None:
        return None
    return occurrence.starts_at - timedelta(hours=booking.policy_vendor_cutoff_hours)


def quote(booking: Booking, occurrence: EventOccurrence, kind: str, now) -> Quote:
    attempt = booking.payment_attempt
    paid = attempt.amount_minor if attempt else 0
    fee = attempt.application_fee_minor if attempt else 0
    deadline = vendor_deadline(booking, occurrence)
    problem = None
    if booking.status == BookingStatus.CANCELLED:
        problem = "already_cancelled"
    elif kind != CancellationKind.EVENT and occurrence.status == OccurrenceStatus.CANCELLED:
        problem = "occurrence_cancelled"
    elif kind == CancellationKind.VENDOR:
        if booking.policy_captured_at is None:
            problem = "manual_review"
        elif booking.policy_vendor_cutoff_hours is None:
            problem = "vendor_cancellation_not_offered"
        elif now >= deadline:
            problem = "cutoff_passed"
    return Quote(
        permitted=problem is None,
        problem=problem,
        refund_rule=RefundRule.PAID_MINUS_FEE if attempt else RefundRule.NO_PAYMENT,
        paid_minor=paid,
        fee_retained_minor=fee,
        refund_minor=max(paid - fee, 0),
        currency=booking.currency,
        deadline=deadline,
        releases_stall=True,
    )


# --- Reading --------------------------------------------------------------------------------


def _vendor_booking(actor: User, business_id: int, booking_id: int):
    membership = vendor_membership(actor, business_id)
    booking = (
        Booking.objects.select_related("occurrence__market", "payment_attempt", "offer__stall")
        .filter(pk=booking_id, vendor_business_id=business_id)
        .first()
    )
    if booking is None:
        raise NotFound("Booking not found.")
    return membership, booking


def _organizer_booking(actor: User, organization_id: int, booking_id: int):
    membership = organization_membership(actor, organization_id)
    booking = (
        Booking.objects.select_related("occurrence__market", "payment_attempt", "offer__stall")
        .filter(pk=booking_id, occurrence__market__organization_id=organization_id)
        .first()
    )
    if booking is None:
        raise NotFound("Booking not found.")
    return membership, booking


@dataclass
class Preview:
    booking: Booking
    quote: Quote
    can_act: bool  # the caller's role may perform this cancellation


def vendor_preview(actor: User, business_id: int, booking_id: int) -> Preview:
    membership, booking = _vendor_booking(actor, business_id, booking_id)
    q = quote(booking, booking.occurrence, CancellationKind.VENDOR, timezone.now())
    return Preview(booking, q, can_act=is_owner(membership))


def organizer_preview(actor: User, organization_id: int, booking_id: int) -> Preview:
    membership, booking = _organizer_booking(actor, organization_id, booking_id)
    q = quote(booking, booking.occurrence, CancellationKind.ORGANIZER, timezone.now())
    return Preview(booking, q, can_act=membership.role in (Role.OWNER, Role.ADMIN))


# --- Cancelling -----------------------------------------------------------------------------


@dataclass
class Outcome:
    cancellation: BookingCancellation
    created: bool


def vendor_cancel(
    actor: User,
    business_id: int,
    booking_id: int,
    *,
    expected_refund_minor: int,
    currency: str,
    reason: str = "",
) -> Outcome:
    """The owner cancels under the booking's terms. The outcome is computed
    again under the locks and must match what the vendor was shown
    (``expected_refund_minor``/``currency``) or it's refused as a stale
    preview. Repeating the request returns the existing cancellation."""
    membership, booking = _vendor_booking(actor, business_id, booking_id)
    if not is_owner(membership):
        raise PermissionDenied("Only the business owner can cancel a booking.")
    return _cancel(
        booking.pk,
        CancellationKind.VENDOR,
        actor,
        requested_as=f"vendor:{membership.role}",
        expected=(expected_refund_minor, currency),
        reason=reason,
    )


def organizer_cancel(
    actor: User,
    organization_id: int,
    booking_id: int,
    *,
    expected_refund_minor: int,
    currency: str,
    reason: str,
    internal_note: str = "",
) -> Outcome:
    membership, booking = _organizer_booking(actor, organization_id, booking_id)
    require_role(membership, Role.OWNER, Role.ADMIN)
    if not reason.strip():
        raise InvalidRequest(
            "Give the vendor a reason for the cancellation.", code="reason_required"
        )
    return _cancel(
        booking.pk,
        CancellationKind.ORGANIZER,
        actor,
        requested_as=f"org:{membership.role}",
        expected=(expected_refund_minor, currency),
        reason=reason,
        internal_note=internal_note,
    )


def _cancel(
    booking_id: int,
    kind: str,
    actor: User,
    *,
    requested_as: str,
    expected: tuple[int, str] | None,
    reason: str = "",
    internal_note: str = "",
    requested_at=None,
) -> Outcome:
    booking = Booking.objects.get(pk=booking_id)
    with transaction.atomic():
        occurrence, reservation = reservations.lock_reservation(booking.reservation_id)
        booking = (
            Booking.objects.select_for_update(of=("self",))
            .select_related("payment_attempt")
            .get(pk=booking_id)
        )
        existing = BookingCancellation.objects.filter(booking=booking).first()
        if existing is not None:
            return Outcome(existing, created=False)  # repeat: same outcome, nothing redone
        now = timezone.now()
        q = quote(booking, occurrence, kind, now)
        if not q.permitted:
            raise Conflict(PROBLEM_MESSAGES[q.problem], code=q.problem)
        if expected is not None and expected != (q.refund_minor, q.currency):
            raise Conflict(
                "The cancellation terms changed since you looked. Review them again.",
                code="stale_preview",
                details=[{"refund_minor": q.refund_minor, "currency": q.currency}],
            )
        reservations.release_for_cancellation(reservation, now)
        booking.status = BookingStatus.CANCELLED
        booking.cancelled_at = now
        booking.save(update_fields=["status", "cancelled_at"])
        refund = None
        if booking.payment_attempt_id and q.refund_minor > 0:
            refund = payments.request_cancellation_refund(
                booking.payment_attempt_id, q.refund_minor
            )
        cancellation = BookingCancellation.objects.create(
            booking=booking,
            kind=kind,
            requested_by=actor,
            requested_as=requested_as[:20],
            reason=reason.strip()[:1000],
            internal_note=internal_note.strip()[:1000],
            requested_at=requested_at or now,
            completed_at=now,
            policy_captured_at=booking.policy_captured_at,
            policy_vendor_cutoff_hours=booking.policy_vendor_cutoff_hours,
            refund_rule=q.refund_rule,
            refund_entitlement_minor=q.refund_minor,
            currency=q.currency,
            refund=refund,
        )
    logger.info("booking=%s cancelled kind=%s refund=%s", booking.pk, kind, refund and refund.pk)
    return Outcome(cancellation, created=True)


def send_refund(cancellation: BookingCancellation) -> None:
    """Contact Stripe for the cancellation's refund, after its transaction
    committed. Failures are recorded on the Refund and retried by
    ``reconcile_payments``."""
    if cancellation.refund_id:
        payments.process_refund(cancellation.refund_id)


# --- Date cancellation -----------------------------------------------------------------------


def record_occurrence_cancellation(occurrence: EventOccurrence, actor: User, role: str) -> None:
    """Receiver of ``markets.signals.occurrence_cancelled``: inside the
    transaction that cancels the date (its row is locked), record one work
    item per reservation still holding or booking a stall. Unpaid holds are
    released right away; checkouts and bookings are processed afterwards in
    bounded batches (``process_occurrence_cancellations``)."""
    now = timezone.now()
    run, _ = OccurrenceCancellation.objects.get_or_create(
        occurrence=occurrence,
        defaults={"requested_by": actor, "requested_as": f"org:{role}", "requested_at": now},
    )
    for reservation in reservations.occupying_for_cancelled_date(occurrence.pk):
        if reservation.status == ReservationStatus.CONFIRMED:
            kind = CancellationItemKind.BOOKING
        elif reservation.payment_pending:
            kind = CancellationItemKind.CHECKOUT
        else:
            kind = CancellationItemKind.HOLD
        item = OccurrenceCancellationItem.objects.create(
            run=run, reservation=reservation, kind=kind, created_at=now
        )
        if kind == CancellationItemKind.HOLD:
            reservations.release_for_cancellation(reservation, now)
            _done(item, now)
    _maybe_complete(run.pk)


def _done(item: OccurrenceCancellationItem, now) -> None:
    item.status = CancellationItemStatus.DONE
    item.processed_at = now
    item.attempts += 1
    item.last_error = ""
    item.save(update_fields=["status", "processed_at", "attempts", "last_error"])


def _maybe_complete(run_id: int) -> None:
    if not OccurrenceCancellationItem.objects.filter(
        run_id=run_id, status=CancellationItemStatus.PENDING
    ).exists():
        OccurrenceCancellation.objects.filter(pk=run_id, completed_at__isnull=True).update(
            completed_at=timezone.now()
        )


def _process_item(item: OccurrenceCancellationItem, *, provider: bool) -> None:
    run = item.run
    if item.kind == CancellationItemKind.BOOKING:
        booking = Booking.objects.filter(reservation_id=item.reservation_id).first()
        outcome = _cancel(
            booking.pk,
            CancellationKind.EVENT,
            run.requested_by,
            requested_as=run.requested_as,
            expected=None,
            reason=run.occurrence.cancellation_message,
            requested_at=run.requested_at,
        )
        with transaction.atomic():
            _done(
                OccurrenceCancellationItem.objects.select_for_update().get(pk=item.pk),
                timezone.now(),
            )
        if provider:
            send_refund(outcome.cancellation)
        return
    # CHECKOUT: settle the payment first (Stripe; no locks), then free the stall.
    if not provider:
        return
    if not payments.settle_checkout_for_cancelled_date(item.reservation_id):
        OccurrenceCancellationItem.objects.filter(pk=item.pk).update(
            attempts=item.attempts + 1, last_error="payment_outcome_pending"
        )
        return
    with transaction.atomic():
        _occurrence, reservation = reservations.lock_reservation(item.reservation_id)
        if reservation.status == ReservationStatus.HELD and not reservation.payment_pending:
            reservations.release_for_cancellation(reservation, timezone.now())
        _done(
            OccurrenceCancellationItem.objects.select_for_update().get(pk=item.pk), timezone.now()
        )


def process_occurrence_cancellations(
    *, limit: int = 100, provider: bool = True, run_id: int | None = None
) -> dict:
    """Work through pending date-cancellation items, oldest first, at most
    ``limit``. With ``provider=False`` only database work runs (bookings are
    cancelled and their refunds recorded, but Stripe isn't called).
    Idempotent: every step checks current state first."""
    counts = {"processed": 0, "waiting": 0, "errors": 0}
    items = OccurrenceCancellationItem.objects.select_related("run__occurrence").filter(
        status=CancellationItemStatus.PENDING
    )
    if run_id is not None:
        items = items.filter(run_id=run_id)
    if not provider:
        items = items.filter(kind=CancellationItemKind.BOOKING)
    touched = set()
    for item in items.order_by("pk")[:limit]:
        touched.add(item.run_id)
        try:
            _process_item(item, provider=provider)
        except Exception as error:  # keep going; the next run retries
            counts["errors"] += 1
            code = getattr(error, "code", type(error).__name__)
            OccurrenceCancellationItem.objects.filter(pk=item.pk).update(
                attempts=item.attempts + 1, last_error=str(code)[:100]
            )
            logger.warning("cancellation item=%s failed: %s", item.pk, code)
            continue
        item.refresh_from_db()
        counts["processed" if item.status == CancellationItemStatus.DONE else "waiting"] += 1
    for run_pk in touched:
        _maybe_complete(run_pk)
    return counts


@dataclass
class Progress:
    run: OccurrenceCancellation | None
    items: dict
    refunds: dict


def occurrence_progress(
    actor: User, organization_id: int, market_id: int, occurrence_id: int
) -> Progress:
    """Any team member may read how a date's cancellation is going."""
    organization_membership(actor, organization_id)
    occurrence = EventOccurrence.objects.filter(
        pk=occurrence_id, market_id=market_id, market__organization_id=organization_id
    ).first()
    if occurrence is None:
        raise NotFound("Date not found.")
    run = OccurrenceCancellation.objects.filter(occurrence=occurrence).first()
    items: dict = {}
    refunds: dict = {}
    if run is not None:
        for kind, status in run.items.values_list("kind", "status"):
            items.setdefault(kind, {}).setdefault(status, 0)
            items[kind][status] += 1
        for status in Refund.objects.filter(
            booking_cancellation__booking__occurrence=occurrence
        ).values_list("status", flat=True):
            refunds[status] = refunds.get(status, 0) + 1
    return Progress(run, items, refunds)


def process_now(actor: User, organization_id: int, market_id: int, occurrence_id: int) -> dict:
    """Organizer's "retry now": one small bounded batch for this date."""
    require_role(organization_membership(actor, organization_id), Role.OWNER, Role.ADMIN)
    run = OccurrenceCancellation.objects.filter(
        occurrence_id=occurrence_id,
        occurrence__market_id=market_id,
        occurrence__market__organization_id=organization_id,
    ).first()
    if run is None:
        raise NotFound("This date hasn't been cancelled.")
    counts = process_occurrence_cancellations(limit=20, run_id=run.pk)
    for refund in Refund.objects.filter(
        booking_cancellation__booking__occurrence_id=occurrence_id,
        status__in=("REQUESTED", "PENDING"),
    ).order_by("pk")[:20]:
        payments.process_refund(refund.pk)
    return counts
