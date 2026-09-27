from datetime import datetime
from decimal import Decimal
from typing import Literal

from ninja import Field, Schema

from core.schemas import InputSchema

PhysicalUnitName = Literal["FT", "M"]


class StallIn(InputSchema):
    # Omit for a new stall; existing stalls keep their id across saves.
    id: int | None = None
    label: str = Field(..., max_length=40)
    description: str = Field("", max_length=500)
    x: int = Field(..., ge=0, le=10_000)
    y: int = Field(..., ge=0, le=10_000)
    width: int = Field(..., ge=1, le=10_000)
    height: int = Field(..., ge=1, le=10_000)
    physical_width: Decimal | None = None
    physical_depth: Decimal | None = None
    physical_unit: PhysicalUnitName | None = None
    enabled: bool = True
    # Integer minor units of the layout currency (e.g. cents). Never a float.
    price_minor: int = Field(..., ge=0)


class LayoutIn(InputSchema):
    # The revision you loaded; null when creating the layout.
    expected_revision: int | None
    canvas_width: int
    canvas_height: int
    currency: str = Field(..., max_length=3)
    stalls: list[StallIn] = Field(default_factory=list, max_length=500)


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
    enabled: bool
    price_minor: int
    created_at: datetime
    updated_at: datetime


class LayoutOut(Schema):
    """Organizer view."""

    id: int
    occurrence_id: int
    market_id: int
    canvas_width: int
    canvas_height: int
    currency: str
    currency_exponent: int
    revision: int
    published: bool
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime
    stalls: list[StallOut]


class PublicStallOut(Schema):
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
    # False: shown on the plan but not offered by the organizer.
    offered: bool
    price_minor: int


class PublicLayoutOut(Schema):
    """Vendor-visible layout. No revision, actors or internal timestamps."""

    occurrence_id: int
    market_id: int
    canvas_width: int
    canvas_height: int
    currency: str
    currency_exponent: int
    stalls: list[PublicStallOut]
