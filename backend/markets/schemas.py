from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal

from ninja import Field, Schema

from core.schemas import InputSchema

MarketTypeName = Literal["FARMERS_MARKET", "POPUP"]
MarketStatusName = Literal["DRAFT", "PUBLISHED", "ARCHIVED"]
OccurrenceStatusName = Literal["SCHEDULED", "CANCELLED"]


class MarketFields(Schema):
    name: str
    description: str
    market_type: MarketTypeName
    venue_name: str
    address_line1: str
    address_line2: str
    city: str
    region: str
    postal_code: str
    country: str
    latitude: Decimal | None
    longitude: Decimal | None
    timezone: str


class MarketOut(MarketFields):
    """Organizer view."""

    id: int
    status: MarketStatusName
    published_at: datetime | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MarketPage(Schema):
    items: list[MarketOut]
    next_cursor: int | None


class MarketCreateIn(InputSchema):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field("", max_length=5000)
    market_type: MarketTypeName
    venue_name: str = Field("", max_length=200)
    address_line1: str = Field("", max_length=200)
    address_line2: str = Field("", max_length=200)
    city: str = Field("", max_length=100)
    region: str = Field("", max_length=100)
    postal_code: str = Field("", max_length=20)
    country: str = Field("", max_length=2)
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    timezone: str = Field(..., max_length=64)


class MarketUpdateIn(InputSchema):
    """Partial update: send only fields to change. "" clears optional text;
    send latitude and longitude together (both null to clear)."""

    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=5000)
    market_type: MarketTypeName | None = None
    venue_name: str | None = Field(None, max_length=200)
    address_line1: str | None = Field(None, max_length=200)
    address_line2: str | None = Field(None, max_length=200)
    city: str | None = Field(None, max_length=100)
    region: str | None = Field(None, max_length=100)
    postal_code: str | None = Field(None, max_length=20)
    country: str | None = Field(None, max_length=2)
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    timezone: str | None = Field(None, max_length=64)


class OccurrenceOut(Schema):
    """Organizer view. Instants are UTC; ``local_*`` are in ``timezone``."""

    id: int
    market_id: int
    starts_at: datetime
    ends_at: datetime
    timezone: str
    local_date: date
    local_start_time: time
    local_end_time: time
    status: OccurrenceStatusName
    cancellation_message: str
    cancelled_at: datetime | None
    series_id: int | None
    created_at: datetime
    updated_at: datetime


class OccurrencePage(Schema):
    items: list[OccurrenceOut]
    next_cursor: datetime | None


class OccurrenceCreateIn(InputSchema):
    starts_at: datetime
    ends_at: datetime


class OccurrenceUpdateIn(InputSchema):
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class OccurrenceCancelIn(InputSchema):
    message: str = Field("", max_length=500)


class SeriesCreateIn(InputSchema):
    """Weekly only. Weekdays are ISO numbers: 1 = Monday ... 7 = Sunday."""

    frequency: Literal["WEEKLY"] = "WEEKLY"
    interval_weeks: int = Field(1, ge=1, le=12)
    weekdays: list[int] = Field(..., min_length=1, max_length=7)
    start_date: date
    end_date: date
    local_start_time: time
    local_end_time: time


class SeriesOut(Schema):
    id: int
    market_id: int
    frequency: Literal["WEEKLY"] = "WEEKLY"
    interval_weeks: int
    weekdays: list[int]
    start_date: date
    end_date: date
    local_start_time: time
    local_end_time: time
    timezone: str
    created_by_user_id: int
    created_at: datetime
    occurrence_count: int


class SeriesGeneratedOut(Schema):
    series: SeriesOut
    created_occurrences: int
    already_existed: bool


# --- Public ------------------------------------------------------------------------


class PublicOrganizer(Schema):
    name: str


class PublicMarketOut(Schema):
    """Published markets only. No organization ids, contacts or moderation data."""

    id: int
    name: str
    description: str
    market_type: MarketTypeName
    venue_name: str
    address_line1: str
    address_line2: str
    city: str
    region: str
    postal_code: str
    country: str
    latitude: Decimal | None
    longitude: Decimal | None
    timezone: str
    organizer: PublicOrganizer


class PublicOccurrenceOut(Schema):
    id: int
    market_id: int
    starts_at: datetime
    ends_at: datetime
    timezone: str
    local_date: date
    local_start_time: time
    local_end_time: time
    status: OccurrenceStatusName
    cancellation_message: str


class PublicOccurrencePage(Schema):
    items: list[PublicOccurrenceOut]
    next_cursor: datetime | None


class PublicOccurrenceDetailOut(PublicOccurrenceOut):
    market: PublicMarketOut
