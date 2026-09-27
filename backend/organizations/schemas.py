from datetime import datetime
from typing import Literal

from ninja import Field, Schema

from core.schemas import InputSchema

# Roles a client may ask for. OWNER only moves via transfer-ownership.
AssignableRole = Literal["ADMIN", "STAFF"]
RoleName = Literal["OWNER", "ADMIN", "STAFF"]


class OrganizationOut(Schema):
    id: int
    name: str
    slug: str
    created_at: datetime


class MyOrganizationOut(Schema):
    organization: OrganizationOut
    membership_id: int
    role: RoleName


class MyOrganizationPage(Schema):
    items: list[MyOrganizationOut]
    next_cursor: int | None


class OrganizationCreateIn(InputSchema):
    name: str = Field(..., min_length=1, max_length=120)
    slug: str | None = Field(None, min_length=1, max_length=60)


class OrganizationUpdateIn(InputSchema):
    name: str = Field(..., min_length=1, max_length=120)


class MemberOut(Schema):
    """Team directory entry. ``email`` is only filled in for OWNER/ADMIN."""

    membership_id: int
    user_id: int
    name: str
    role: RoleName
    joined_at: datetime
    email: str | None = None


class MemberPage(Schema):
    items: list[MemberOut]
    next_cursor: int | None


class MemberRoleIn(InputSchema):
    role: AssignableRole


class TransferOwnershipIn(InputSchema):
    membership_id: int


class InvitationCreateIn(InputSchema):
    email: str = Field(..., max_length=254)
    role: AssignableRole


class InvitationOut(Schema):
    id: int
    email: str
    role: AssignableRole
    status: Literal["pending", "expired"]
    invited_by_user_id: int
    expires_at: datetime
    last_sent_at: datetime | None
    created_at: datetime


class InvitationPage(Schema):
    items: list[InvitationOut]
    next_cursor: int | None


class InvitationSentOut(Schema):
    invitation: InvitationOut
    email_sent: bool


class InvitationAcceptIn(InputSchema):
    token: str = Field(..., min_length=1, max_length=128)


class InvitationAcceptOut(Schema):
    organization: OrganizationOut
    membership_id: int
    role: RoleName
    already_member: bool
