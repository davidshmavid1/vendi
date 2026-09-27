"""Booking cancellation endpoints (Phase 14). Handlers only translate HTTP;
rules live in bookings/cancellations.py.

- Vendor: /api/v1/vendors/{business_id}/bookings/{id}/cancellation (preview)
  and …/cancel (OWNER)
- Organizer: /api/v1/organizations/{org_id}/bookings/{id}/cancellation and
  …/cancel (OWNER, ADMIN); date cancellation progress and "retry now" under
  …/markets/{m}/occurrences/{o}/cancellation. Dates are cancelled with the
  existing markets endpoint (…/occurrences/{o}/cancel).

There is no endpoint for an arbitrary refund amount: refunds follow only
from a cancellation and its terms.
"""

from django.utils import timezone
from ninja import Router

from bookings import cancellations
from bookings.models import Booking
from bookings.schemas import (
    CancellationPreviewOut,
    DateCancellationOut,
    OrganizerCancelIn,
    VendorCancelIn,
)
from core.auth import session_auth
from core.schemas import ErrorOut
from layouts.money import exponent
from payments.api import booking_out
from payments.models import NEEDS_OPERATOR, UNRESOLVED_REFUND
from payments.schemas import BookingOut

vendor_router = Router(tags=["bookings"], auth=session_auth)
organizer_router = Router(tags=["bookings"], auth=session_auth)

_ERRORS = {400: ErrorOut, 401: ErrorOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut}


def _booking(pk: int, *, organizer: bool) -> dict:
    booking = Booking.objects.select_related(
        "occurrence__market",
        "offer__stall",
        "vendor_business",
        "payment_attempt",
        "cancellation__refund",
    ).get(pk=pk)
    return booking_out(booking, organizer=organizer)


def _preview_out(preview: cancellations.Preview, *, organizer: bool) -> dict:
    q = preview.quote
    return {
        "booking": _booking(preview.booking.pk, organizer=organizer),
        "permitted": q.permitted,
        "problem": q.problem,
        "message": cancellations.PROBLEM_MESSAGES.get(q.problem) if q.problem else None,
        "can_act": preview.can_act,
        "refund_rule": q.refund_rule,
        "paid_minor": q.paid_minor,
        "fee_retained_minor": q.fee_retained_minor,
        "refund_minor": q.refund_minor,
        "currency": q.currency,
        "currency_exponent": exponent(q.currency),
        "vendor_deadline": q.deadline,
        "releases_stall": q.releases_stall,
        "server_time": timezone.now(),
    }


_VENDOR = "/{business_id}/bookings/{booking_id}"


@vendor_router.get(f"{_VENDOR}/cancellation", response={200: CancellationPreviewOut, **_ERRORS})
def vendor_preview(request, business_id: int, booking_id: int):
    """What cancelling now would do (any member may look)."""
    preview = cancellations.vendor_preview(request.auth, business_id, booking_id)
    return _preview_out(preview, organizer=False)


@vendor_router.post(f"{_VENDOR}/cancel", response={200: BookingOut, 422: ErrorOut, **_ERRORS})
def vendor_cancel(request, business_id: int, booking_id: int, payload: VendorCancelIn):
    """Cancel under the booking's terms. Send back the previewed refund
    amount and currency; 409 stale_preview if the outcome changed. Repeating
    it returns the booking as already cancelled."""
    outcome = cancellations.vendor_cancel(
        request.auth,
        business_id,
        booking_id,
        expected_refund_minor=payload.expected_refund_minor,
        currency=payload.currency,
        reason=payload.reason,
    )
    if outcome.created:
        cancellations.send_refund(outcome.cancellation)
    return _booking(booking_id, organizer=False)


_ORG = "/{organization_id}/bookings/{booking_id}"


@organizer_router.get(f"{_ORG}/cancellation", response={200: CancellationPreviewOut, **_ERRORS})
def organizer_preview(request, organization_id: int, booking_id: int):
    preview = cancellations.organizer_preview(request.auth, organization_id, booking_id)
    return _preview_out(preview, organizer=True)


@organizer_router.post(f"{_ORG}/cancel", response={200: BookingOut, 422: ErrorOut, **_ERRORS})
def organizer_cancel(request, organization_id: int, booking_id: int, payload: OrganizerCancelIn):
    """Cancel a booking for the vendor (full refund of the amount paid minus
    Vendi's fee). OWNER and ADMIN only."""
    outcome = cancellations.organizer_cancel(
        request.auth,
        organization_id,
        booking_id,
        expected_refund_minor=payload.expected_refund_minor,
        currency=payload.currency,
        reason=payload.reason,
        internal_note=payload.internal_note,
    )
    if outcome.created:
        cancellations.send_refund(outcome.cancellation)
    return _booking(booking_id, organizer=True)


_DATE = "/{organization_id}/markets/{market_id}/occurrences/{occurrence_id}/cancellation"


def _progress_out(occurrence_id: int, progress: cancellations.Progress) -> dict:
    run = progress.run
    pending_items = any(
        status == "PENDING" and count
        for by_status in progress.items.values()
        for status, count in by_status.items()
    )
    pending_refunds = any(
        progress.refunds.get(status) for status in (*UNRESOLVED_REFUND, *NEEDS_OPERATOR)
    )
    return {
        "occurrence_id": occurrence_id,
        "cancelled": run is not None,
        "requested_at": run.requested_at if run else None,
        "completed_at": run.completed_at if run else None,
        "items": progress.items,
        "refunds": progress.refunds,
        "pending": pending_items or pending_refunds,
    }


@organizer_router.get(_DATE, response={200: DateCancellationOut, **_ERRORS})
def date_cancellation(request, organization_id: int, market_id: int, occurrence_id: int):
    """How a cancelled date's cleanup is going (any team member)."""
    progress = cancellations.occurrence_progress(
        request.auth, organization_id, market_id, occurrence_id
    )
    return _progress_out(occurrence_id, progress)


@organizer_router.post(f"{_DATE}/process", response={200: DateCancellationOut, **_ERRORS})
def process_date_cancellation(request, organization_id: int, market_id: int, occurrence_id: int):
    """Retry now: one small batch of this date's pending work and refunds."""
    cancellations.process_now(request.auth, organization_id, market_id, occurrence_id)
    progress = cancellations.occurrence_progress(
        request.auth, organization_id, market_id, occurrence_id
    )
    return _progress_out(occurrence_id, progress)
