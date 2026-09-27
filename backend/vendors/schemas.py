from datetime import datetime
from typing import Literal

from ninja import Field, Schema

from core.schemas import InputSchema
from vendors.models import Category

CategoryName = Literal[tuple(Category.values)]  # type: ignore[valid-type]
VendorRoleName = Literal["OWNER", "MEMBER"]


class VendorBusinessOut(Schema):
    """Private profile, shown only to the business's members."""

    id: int
    name: str
    description: str
    category: CategoryName
    contact_email: str
    phone: str
    website: str
    city: str
    region: str
    created_at: datetime
    updated_at: datetime


class MyVendorBusinessOut(Schema):
    business: VendorBusinessOut
    membership_id: int
    role: VendorRoleName


class MyVendorBusinessPage(Schema):
    items: list[MyVendorBusinessOut]
    next_cursor: int | None


class VendorBusinessCreateIn(InputSchema):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field("", max_length=2000)
    category: CategoryName
    contact_email: str = Field(..., max_length=254)
    phone: str = Field("", max_length=32)
    website: str = Field("", max_length=200)
    city: str = Field("", max_length=100)
    region: str = Field("", max_length=100)


class VendorBusinessUpdateIn(InputSchema):
    """Partial update: send only the fields to change. Use "" to clear an
    optional field."""

    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=2000)
    category: CategoryName | None = None
    contact_email: str | None = Field(None, max_length=254)
    phone: str | None = Field(None, max_length=32)
    website: str | None = Field(None, max_length=200)
    city: str | None = Field(None, max_length=100)
    region: str | None = Field(None, max_length=100)


class VendorMemberOut(Schema):
    """Member directory entry. ``email`` is only filled in for the owner."""

    membership_id: int
    user_id: int
    name: str
    role: VendorRoleName
    joined_at: datetime
    email: str | None = None


class VendorMemberPage(Schema):
    items: list[VendorMemberOut]
    next_cursor: int | None


class TransferOwnershipIn(InputSchema):
    membership_id: int


class VendorInvitationCreateIn(InputSchema):
    email: str = Field(..., max_length=254)


class VendorInvitationOut(Schema):
    id: int
    email: str
    role: Literal["MEMBER"] = "MEMBER"
    status: Literal["pending", "expired"]
    invited_by_user_id: int
    expires_at: datetime
    last_sent_at: datetime | None
    created_at: datetime


class VendorInvitationPage(Schema):
    items: list[VendorInvitationOut]
    next_cursor: int | None


class VendorInvitationSentOut(Schema):
    invitation: VendorInvitationOut
    email_sent: bool


class VendorInvitationAcceptIn(InputSchema):
    token: str = Field(..., min_length=1, max_length=128)


class VendorInvitationAcceptOut(Schema):
    business: VendorBusinessOut
    membership_id: int
    role: VendorRoleName
    already_member: bool
