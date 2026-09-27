"""Organization and team endpoints. Every route requires a session (CSRF on
mutations) and delegates permission checks to organizations/services.py."""

from django.conf import settings
from ninja import Router, Status

from accounts.throttles import UserThrottle
from core.auth import session_auth
from core.pagination import paginate
from core.schemas import ErrorOut
from organizations import services
from organizations.models import OrganizationMembership
from organizations.permissions import can_manage_team
from organizations.schemas import (
    InvitationAcceptIn,
    InvitationAcceptOut,
    InvitationCreateIn,
    InvitationPage,
    InvitationSentOut,
    MemberOut,
    MemberPage,
    MemberRoleIn,
    MyOrganizationOut,
    MyOrganizationPage,
    OrganizationCreateIn,
    OrganizationUpdateIn,
    TransferOwnershipIn,
)

router = Router(tags=["organizations"], auth=session_auth)
invitations_router = Router(tags=["organizations"], auth=session_auth)

_ERRORS = {400: ErrorOut, 401: ErrorOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut}
_RATES = settings.ORGANIZATION_RATE_LIMITS


def _my_organization(membership: OrganizationMembership) -> dict:
    return {
        "organization": membership.organization,
        "membership_id": membership.pk,
        "role": membership.role,
    }


def _member(member: OrganizationMembership, *, show_email: bool) -> dict:
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
        "role": invitation.role,
        "status": "pending" if invitation.is_usable else "expired",
        "invited_by_user_id": invitation.invited_by_id,
        "expires_at": invitation.expires_at,
        "last_sent_at": invitation.last_sent_at,
        "created_at": invitation.created_at,
    }


# --- Organizations ------------------------------------------------------------


@router.post("", response={201: MyOrganizationOut, 422: ErrorOut, **_ERRORS})
def create_organization(request, payload: OrganizationCreateIn):
    membership = services.create_organization(request.auth, name=payload.name, slug=payload.slug)
    return Status(201, _my_organization(membership))


@router.get("", response={200: MyOrganizationPage, 401: ErrorOut})
def list_organizations(request, cursor: int | None = None, limit: int | None = None):
    items, next_cursor = paginate(
        services.list_memberships(request.auth), cursor=cursor, limit=limit
    )
    return {"items": [_my_organization(m) for m in items], "next_cursor": next_cursor}


@router.get("/{organization_id}", response={200: MyOrganizationOut, **_ERRORS})
def get_organization(request, organization_id: int):
    return _my_organization(services.get_organization(request.auth, organization_id))


@router.patch("/{organization_id}", response={200: MyOrganizationOut, 422: ErrorOut, **_ERRORS})
def update_organization(request, organization_id: int, payload: OrganizationUpdateIn):
    membership = services.update_organization(request.auth, organization_id, name=payload.name)
    return _my_organization(membership)


# --- Members ------------------------------------------------------------------


@router.get("/{organization_id}/members", response={200: MemberPage, **_ERRORS})
def list_members(
    request, organization_id: int, cursor: int | None = None, limit: int | None = None
):
    actor, members = services.list_members(request.auth, organization_id)
    items, next_cursor = paginate(members, cursor=cursor, limit=limit)
    show_email = can_manage_team(actor)
    return {"items": [_member(m, show_email=show_email) for m in items], "next_cursor": next_cursor}


@router.patch(
    "/{organization_id}/members/{membership_id}",
    response={200: MemberOut, 422: ErrorOut, **_ERRORS},
)
def change_member_role(request, organization_id: int, membership_id: int, payload: MemberRoleIn):
    member = services.change_role(request.auth, organization_id, membership_id, role=payload.role)
    return _member(member, show_email=True)


@router.delete("/{organization_id}/members/{membership_id}", response={204: None, **_ERRORS})
def remove_member(request, organization_id: int, membership_id: int):
    services.remove_member(request.auth, organization_id, membership_id)
    return Status(204, None)


@router.post("/{organization_id}/leave", response={204: None, **_ERRORS})
def leave_organization(request, organization_id: int):
    services.leave_organization(request.auth, organization_id)
    return Status(204, None)


@router.post(
    "/{organization_id}/transfer-ownership",
    response={200: MemberOut, 422: ErrorOut, **_ERRORS},
)
def transfer_ownership(request, organization_id: int, payload: TransferOwnershipIn):
    member = services.transfer_ownership(request.auth, organization_id, payload.membership_id)
    return _member(member, show_email=True)


# --- Invitations ----------------------------------------------------------------


@router.post(
    "/{organization_id}/invitations",
    response={201: InvitationSentOut, 422: ErrorOut, 429: ErrorOut, **_ERRORS},
    throttle=[UserThrottle("invitation_create_user", _RATES)],
)
def create_invitation(request, organization_id: int, payload: InvitationCreateIn):
    sent = services.create_invitation(
        request.auth, organization_id, email=payload.email, role=payload.role
    )
    return Status(201, {"invitation": _invitation(sent.invitation), "email_sent": sent.email_sent})


@router.get("/{organization_id}/invitations", response={200: InvitationPage, **_ERRORS})
def list_invitations(
    request, organization_id: int, cursor: int | None = None, limit: int | None = None
):
    items, next_cursor = paginate(
        services.list_invitations(request.auth, organization_id), cursor=cursor, limit=limit
    )
    return {"items": [_invitation(i) for i in items], "next_cursor": next_cursor}


@router.post(
    "/{organization_id}/invitations/{invitation_id}/resend",
    response={200: InvitationSentOut, 429: ErrorOut, **_ERRORS},
    throttle=[UserThrottle("invitation_resend_user", _RATES)],
)
def resend_invitation(request, organization_id: int, invitation_id: int):
    sent = services.resend_invitation(request.auth, organization_id, invitation_id)
    return {"invitation": _invitation(sent.invitation), "email_sent": sent.email_sent}


@router.delete("/{organization_id}/invitations/{invitation_id}", response={204: None, **_ERRORS})
def revoke_invitation(request, organization_id: int, invitation_id: int):
    services.revoke_invitation(request.auth, organization_id, invitation_id)
    return Status(204, None)


@invitations_router.post("/accept", response={200: InvitationAcceptOut, 422: ErrorOut, **_ERRORS})
def accept_invitation(request, payload: InvitationAcceptIn):
    result = services.accept_invitation(request.auth, payload.token)
    return {
        "organization": result.membership.organization,
        "membership_id": result.membership.pk,
        "role": result.membership.role,
        "already_member": result.already_member,
    }
