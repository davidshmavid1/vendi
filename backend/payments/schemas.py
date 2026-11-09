from datetime import datetime
from typing import Literal

from ninja import Schema

from reservations.schemas import ReservationDate, ReservationOut, ReservationStall

# What the vendor should see, derived from the reservation, its latest
# payment attempt, booking and refund (see api._state).
PaymentStateName = Literal[
    "HOLDING",  # held, not paid yet
    "CHECKOUT_OPEN",  # a Checkout Session can be paid now
    "PROCESSING",  # outcome not known yet (never treat as paid)
    "BOOKED",
    "REFUND_PENDING",  # paid, but the stall couldn't be confirmed
    "REFUNDED",
    "REFUND_FAILED",  # needs Vendi support
    "EXPIRED",
    "RELEASED",
]


class PaymentOut(Schema):
    id: int
    status: Literal["CREATING", "OPEN", "SUCCEEDED", "EXPIRED", "CANCELED", "FAILED"]
    fulfillment: Literal["FULFILLED", "UNFULFILLED"] | None
    # Total charged = stall price + Vendi's fee.
    amount_minor: int
    stall_price_minor: int
    fee_minor: int
    currency: str
    currency_exponent: int
    session_expires_at: datetime
    succeeded_at: datetime | None
    # Only while the session can be paid, and only for the business owner.
    checkout_url: str | None


class RefundOut(Schema):
    status: Literal["REQUESTED", "PENDING", "SUCCEEDED", "FAILED", "CANCELED"]
    amount_minor: int
    currency: str


class BookingBusiness(Schema):
    id: int
    name: str


class BookingOut(Schema):
    id: int
    reservation_id: int
    stall: ReservationStall
    occurrence: ReservationDate
    vendor_business: BookingBusiness
    price_minor: int
    currency: str
    currency_exponent: int
    payment_required: bool
    paid_at: datetime | None
    created_at: datetime


class BookingPage(Schema):
    items: list[BookingOut]
    next_cursor: int | None


class CheckoutQuote(Schema):
    """What checkout will charge for a held stall: the stall price plus
    Vendi's fee, added on top."""

    stall_price_minor: int
    fee_minor: int
    total_minor: int
    currency: str
    currency_exponent: int


class PaymentStatusOut(Schema):
    state: PaymentStateName
    payment_required: bool
    # Present while the stall is held and the organizer can take payments.
    quote: CheckoutQuote | None
    reservation: ReservationOut
    payment: PaymentOut | None
    booking: BookingOut | None
    refund: RefundOut | None


class WebhookOut(Schema):
    received: bool
