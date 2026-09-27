from datetime import datetime
from typing import Literal

from ninja import Field, Schema

from core.schemas import InputSchema
from payments.schemas import BookingOut


class CancellationPreviewOut(Schema):
    """Server-computed outcome of cancelling now. Nothing here is trusted
    back from the client except the refund amount and currency it echoes,
    which must still match when the cancellation is confirmed."""

    booking: BookingOut
    permitted: bool
    # Why not (stable code), and a message safe to show.
    problem: str | None
    message: str | None
    # The caller's role may perform it (e.g. vendor members may only view).
    can_act: bool
    refund_rule: Literal["PAID_MINUS_FEE", "NO_PAYMENT"]
    paid_minor: int
    fee_retained_minor: int
    refund_minor: int
    currency: str
    currency_exponent: int
    vendor_deadline: datetime | None
    releases_stall: bool
    server_time: datetime


class VendorCancelIn(InputSchema):
    # Echo the preview: a changed outcome is refused as a stale preview.
    expected_refund_minor: int = Field(..., ge=0)
    currency: str = Field(..., min_length=3, max_length=3)
    reason: str = Field("", max_length=1000)


class OrganizerCancelIn(InputSchema):
    expected_refund_minor: int = Field(..., ge=0)
    currency: str = Field(..., min_length=3, max_length=3)
    # Shown to the vendor.
    reason: str = Field(..., min_length=1, max_length=1000)
    # Organizer-only; never shown to vendors.
    internal_note: str = Field("", max_length=1000)


class DateCancellationOut(Schema):
    occurrence_id: int
    cancelled: bool
    requested_at: datetime | None
    completed_at: datetime | None
    # {kind: {status: count}} for HOLD, CHECKOUT and BOOKING work items.
    items: dict[str, dict[str, int]]
    # {status: count} of the date's cancellation refunds.
    refunds: dict[str, int]
    # Work or refunds still to settle (or needing an operator).
    pending: bool
