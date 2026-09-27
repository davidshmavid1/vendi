from datetime import datetime
from typing import Literal

from ninja import Field, Schema

from core.schemas import InputSchema

ReservationStatusName = Literal["HELD", "EXPIRED", "RELEASED", "CONFIRMED"]


class HoldIn(InputSchema):
    offer_id: int
    # Idempotency key chosen by the client for this attempt (e.g. a UUID).
    request_key: str = Field(..., min_length=1, max_length=64)


class ReservationStall(Schema):
    id: int
    label: str


class ReservationDate(Schema):
    id: int
    market_id: int
    market_name: str
    starts_at: datetime
    ends_at: datetime
    timezone: str


class ReservationOut(Schema):
    id: int
    # EXPIRED as soon as expires_at passes, even before housekeeping runs.
    status: ReservationStatusName
    offer_id: int
    stall: ReservationStall
    occurrence: ReservationDate
    application_id: int
    price_minor: int
    currency: str
    currency_exponent: int
    expires_at: datetime
    created_at: datetime
    expired_at: datetime | None
    released_at: datetime | None
    confirmed_at: datetime | None
    # The server's clock when this response was made, so countdowns don't
    # depend on the visitor's clock.
    server_time: datetime


class ReservationPage(Schema):
    items: list[ReservationOut]
    next_cursor: int | None


class StallAvailability(Schema):
    stall_id: int
    offer_id: int | None
    status: Literal["available", "unavailable", "not_offered"]


class AvailabilityOut(Schema):
    occurrence_id: int
    items: list[StallAvailability]
