from datetime import datetime
from decimal import Decimal
from typing import Literal

from ninja import Field, Schema

from core.schemas import InputSchema

PhysicalUnitName = Literal["FT", "M"]


class StallFields(InputSchema):
    label: str = Field(..., max_length=40)
    description: str = Field("", max_length=500)
    x: int = Field(..., ge=0, le=10_000)
    y: int = Field(..., ge=0, le=10_000)
    width: int = Field(..., ge=1, le=10_000)
    height: int = Field(..., ge=1, le=10_000)
    physical_width: Decimal | None = None
    physical_depth: Decimal | None = None
    physical_unit: PhysicalUnitName | None = None


class StallIn(StallFields):
    # Omit for a new stall; existing stalls keep their id across saves.
    id: int | None = None


class VersionCreateIn(InputSchema):
    canvas_width: int = 100
    canvas_height: int = 60
    stalls: list[StallFields] = Field(default_factory=list, max_length=500)
    # Copy this version's canvas and stalls instead of using the fields above.
    copy_of: int | None = None


class VersionIn(InputSchema):
    expected_revision: int
    canvas_width: int
    canvas_height: int
    stalls: list[StallIn] = Field(default_factory=list, max_length=500)


class OfferIn(InputSchema):
    stall_id: int
    # Integer minor units of the currency (e.g. cents). Never a float.
    price_minor: int = Field(..., ge=0)
    enabled: bool = True


class DateLayoutIn(InputSchema):
    # The revision you loaded; null when the date has no layout yet.
    expected_revision: int | None
    layout_version_id: int
    currency: str = Field(..., max_length=3)
    offers: list[OfferIn] = Field(default_factory=list, max_length=500)


class PublishIn(InputSchema):
    expected_revision: int


class StallOut(Schema):
    id: int
    label: str
    description: str
    x: int
    y: int
    width: int
    height: int
    physical_width: Decimal | None
    physical_depth: Decimal | None
    physical_unit: PhysicalUnitName | None


class VersionSummary(Schema):
    id: int
    number: int
    canvas_width: int
    canvas_height: int
    revision: int
    locked: bool
    based_on_id: int | None
    stall_count: int
    date_count: int
    updated_at: datetime


class VersionList(Schema):
    items: list[VersionSummary]


class VersionOut(Schema):
    id: int
    market_id: int
    number: int
    canvas_width: int
    canvas_height: int
    revision: int
    locked: bool
    locked_at: datetime | None
    based_on_id: int | None
    created_at: datetime
    updated_at: datetime
    stalls: list[StallOut]


class OfferOut(Schema):
    id: int
    stall_id: int
    price_minor: int
    currency: str
    enabled: bool


class DateLayoutOut(Schema):
    """Organizer view of one date's layout and offers."""

    occurrence_id: int
    market_id: int
    layout_version: VersionOut
    currency: str | None
    currency_exponent: int | None
    revision: int
    published: bool
    published_at: datetime | None
    offers: list[OfferOut]


class PublicStallOut(Schema):
    id: int
    # The offer to reference when reserving (later phases).
    offer_id: int | None
    label: str
    description: str
    x: int
    y: int
    width: int
    height: int
    physical_width: Decimal | None
    physical_depth: Decimal | None
    physical_unit: PhysicalUnitName | None
    # False: shown on the plan but not offered on this date.
    offered: bool
    price_minor: int | None


class PublicLayoutOut(Schema):
    """Vendor-visible layout. No revisions, versions, actors or timestamps."""

    occurrence_id: int
    market_id: int
    canvas_width: int
    canvas_height: int
    currency: str
    currency_exponent: int
    stalls: list[PublicStallOut]
