"""Vendor business endpoints. Every route requires a session (CSRF on
mutations) and delegates permission checks to vendors/services.py."""

from django.conf import settings
from ninja import Router, Status

from accounts.throttles import UserThrottle
from core.auth import session_auth
from core.pagination import paginate
from core.schemas import ErrorOut
from vendors import services
from vendors.models import VendorMembership
from vendors.permissions import is_owner
from vendors.schemas import (
    MyVendorBusinessOut,
    MyVendorBusinessPage,
    TransferOwnershipIn,
    VendorBusinessCreateIn,
    VendorBusinessUpdateIn,
    VendorInvitationAcceptIn,
    VendorInvitationAcceptOut,
    VendorInvitationCreateIn,
    VendorInvitationPage,
    VendorInvitationSentOut,
    VendorMemberOut,
    VendorMemberPage,
)

router = Router(tags=["vendors"], auth=session_auth)
invitations_router = Router(tags=["vendors"], auth=session_auth)

_ERRORS = {400: ErrorOut, 401: ErrorOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut}
_RATES = settings.VENDOR_RATE_LIMITS


def _my_business(membership: VendorMembership) -> dict:
    return {
        "business": membership.business,
        "membership_id": membership.pk,
        "role": membership.role,
    }


def _member(member: VendorMembership, *, show_email: bool) -> dict:
    return {
        "membership_id": member.pk,
        "user_id": member.user_id,
        "name": member.user.get_full_name(),
        "role": member.role,
        "joined_at": member.created_at,
        "email": member.user.email if show_email else None,
    }


def _invitation(invitation) -> dict:
    return {
        "id": invitation.pk,
        "email": invitation.email,
        "status": "pending" if invitation.is_usable else "expired",
        "invited_by_user_id": invitation.invited_by_id,
        "expires_at": invitation.expires_at,
        "last_sent_at": invitation.last_sent_at,
        "created_at": invitation.created_at,
    }


# --- Profiles -----------------------------------------------------------------


@router.post("", response={201: MyVendorBusinessOut, 422: ErrorOut, **_ERRORS})
def create_business(request, payload: VendorBusinessCreateIn):
    membership = services.create_business(request.auth, **payload.dict())
    return Status(201, _my_business(membership))


@router.get("", response={200: MyVendorBusinessPage, 401: ErrorOut})
def list_businesses(request, cursor: int | None = None, limit: int | None = None):
    items, next_cursor = paginate(
        services.list_memberships(request.auth), cursor=cursor, limit=limit
    )
    return {"items": [_my_business(m) for m in items], "next_cursor": next_cursor}


@router.get("/{business_id}", response={200: MyVendorBusinessOut, **_ERRORS})
def get_business(request, business_id: int):
    return _my_business(services.get_business(request.auth, business_id))


@router.patch("/{business_id}", response={200: MyVendorBusinessOut, 422: ErrorOut, **_ERRORS})
def update_business(request, business_id: int, payload: VendorBusinessUpdateIn):
    changes = payload.dict(exclude_unset=True)
    return _my_business(services.update_business(request.auth, business_id, **changes))


# --- Members ------------------------------------------------------------------


@router.get("/{business_id}/members", response={200: VendorMemberPage, **_ERRORS})
def list_members(request, business_id: int, cursor: int | None = None, limit: int | None = None):
    actor, members = services.list_members(request.auth, business_id)
    items, next_cursor = paginate(members, cursor=cursor, limit=limit)
    show_email = is_owner(actor)
    return {"items": [_member(m, show_email=show_email) for m in items], "next_cursor": next_cursor}


@router.delete("/{business_id}/members/{membership_id}", response={204: None, **_ERRORS})
def remove_member(request, business_id: int, membership_id: int):
    services.remove_member(request.auth, business_id, membership_id)
    return Status(204, None)


@router.post("/{business_id}/leave", response={204: None, **_ERRORS})
def leave_business(request, business_id: int):
    services.leave_business(request.auth, business_id)
    return Status(204, None)


@router.post(
    "/{business_id}/transfer-ownership", response={200: VendorMemberOut, 422: ErrorOut, **_ERRORS}
)
def transfer_ownership(request, business_id: int, payload: TransferOwnershipIn):
    member = services.transfer_ownership(request.auth, business_id, payload.membership_id)
    return _member(member, show_email=True)


# --- Invitations ----------------------------------------------------------------


@router.post(
    "/{business_id}/invitations",
    response={201: VendorInvitationSentOut, 422: ErrorOut, 429: ErrorOut, **_ERRORS},
    throttle=[UserThrottle("invitation_create_user", _RATES)],
)
def create_invitation(request, business_id: int, payload: VendorInvitationCreateIn):
    sent = services.create_invitation(request.auth, business_id, email=payload.email)
    return Status(201, {"invitation": _invitation(sent.invitation), "email_sent": sent.email_sent})


@router.get("/{business_id}/invitations", response={200: VendorInvitationPage, **_ERRORS})
def list_invitations(
    request, business_id: int, cursor: int | None = None, limit: int | None = None
):
    items, next_cursor = paginate(
        services.list_invitations(request.auth, business_id), cursor=cursor, limit=limit
    )
    return {"items": [_invitation(i) for i in items], "next_cursor": next_cursor}


@router.post(
    "/{business_id}/invitations/{invitation_id}/resend",
    response={200: VendorInvitationSentOut, 429: ErrorOut, **_ERRORS},
    throttle=[UserThrottle("invitation_resend_user", _RATES)],
)
def resend_invitation(request, business_id: int, invitation_id: int):
    sent = services.resend_invitation(request.auth, business_id, invitation_id)
    return {"invitation": _invitation(sent.invitation), "email_sent": sent.email_sent}


@router.delete("/{business_id}/invitations/{invitation_id}", response={204: None, **_ERRORS})
def revoke_invitation(request, business_id: int, invitation_id: int):
    services.revoke_invitation(request.auth, business_id, invitation_id)
    return Status(204, None)


@invitations_router.post(
    "/accept", response={200: VendorInvitationAcceptOut, 422: ErrorOut, **_ERRORS}
)
def accept_invitation(request, payload: VendorInvitationAcceptIn):
    result = services.accept_invitation(request.auth, payload.token)
    return {
        "business": result.membership.business,
        "membership_id": result.membership.pk,
        "role": result.membership.role,
        "already_member": result.already_member,
    }
