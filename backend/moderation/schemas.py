from datetime import datetime
from typing import Literal

from ninja import Field, Schema

from core.schemas import InputSchema


class RestrictionCreateIn(InputSchema):
    """Give exactly one of account_id or vendor_business_id."""

    account_id: int | None = None
    vendor_business_id: int | None = None
    reason: str = Field(..., min_length=1, max_length=1000)
    expires_at: datetime | None = None


class RestrictionRevokeIn(InputSchema):
    note: str = Field("", max_length=1000)


class RestrictionOut(Schema):
    """Moderator-only view, including the internal reason."""

    id: int
    target_type: Literal["account", "vendor_business"]
    account_id: int | None
    vendor_business_id: int | None
    status: Literal["effective", "expired", "revoked"]
    reason: str
    created_by_user_id: int
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    revoked_by_user_id: int | None
    revocation_note: str


class RestrictionPage(Schema):
    items: list[RestrictionOut]
    next_cursor: int | None
