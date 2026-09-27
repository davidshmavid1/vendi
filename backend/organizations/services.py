"""Organization and team operations.

Every operation takes the acting user, re-reads their membership from the
database and checks it (organizations/permissions.py). Operations that change
memberships or invitations lock the organization row first, then re-check
the actor inside that transaction, so concurrent removals, demotions and
ownership transfers can't interleave. Emails are sent after commit.
"""

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.text import slugify

from accounts.models import User, normalize_email
from core.exceptions import Conflict, InvalidRequest, NotFound, PermissionDenied
from organizations import emails
from organizations.models import (
    InvitationStatus,
    Organization,
    OrganizationAuditEvent,
    OrganizationInvitation,
    OrganizationMembership,
    Role,
)
from organizations.permissions import (
    MANAGED_ROLES,
    lock_organization,
    membership_for,
    require_manages,
    require_role,
)

SLUG_MAX_LENGTH = 60
SLUG_PATTERN = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")  # same as the DB CHECK


def _audit(organization_id, actor, action, subject, **details) -> None:
    OrganizationAuditEvent.objects.create(
        organization_id=organization_id,
        actor=actor,
        action=action,
        subject_type=subject._meta.model_name,
        subject_id=subject.pk,
        details=details,
    )


def _require_verified(user: User) -> None:
    if not user.is_email_verified:
        raise PermissionDenied(
            "Confirm your email address before doing this.", code="email_not_verified"
        )


# --- Organizations ------------------------------------------------------------


def _slug_candidates(name: str, requested: str | None):
    if requested is not None:
        yield requested
        return
    base = slugify(name)[: SLUG_MAX_LENGTH - 7].strip("-") or "organization"
    yield base
    for _ in range(5):
        yield f"{base}-{secrets.token_hex(3)}"


def create_organization(
    actor: User, *, name: str, slug: str | None = None
) -> OrganizationMembership:
    """Create an organization with ``actor`` as its owner, atomically.

    A requested slug that is taken is a conflict. Without one, the slug comes
    from the name, with a random suffix if taken. The unique index decides;
    no availability pre-check.
    """
    _require_verified(actor)
    name = name.strip()
    if not name:
        raise InvalidRequest("Enter an organization name.", code="name_invalid")
    if slug is not None and not (len(slug) <= SLUG_MAX_LENGTH and SLUG_PATTERN.fullmatch(slug)):
        raise InvalidRequest(
            "Use lowercase letters, numbers and single hyphens.", code="slug_invalid"
        )

    for candidate in _slug_candidates(name, slug):
        try:
            with transaction.atomic():
                organization = Organization.objects.create(name=name, slug=candidate)
                membership = OrganizationMembership.objects.create(
                    organization=organization, user=actor, role=Role.OWNER
                )
                _audit(organization.pk, actor, "organization.created", organization)
            return membership
        except IntegrityError:
            if slug is not None:
                raise Conflict("That URL name is already taken.", code="slug_taken") from None
    raise Conflict("Could not find a free URL name. Choose one.", code="slug_taken")


def list_memberships(actor: User):
    """The actor's memberships (with organization), for "my organizations"."""
    return OrganizationMembership.objects.select_related("organization").filter(user=actor)


def get_organization(actor: User, organization_id: int) -> OrganizationMembership:
    return membership_for(actor, organization_id)


def update_organization(actor: User, organization_id: int, *, name: str) -> OrganizationMembership:
    with transaction.atomic():
        lock_organization(organization_id)
        membership = membership_for(actor, organization_id)
        require_role(membership, Role.OWNER, Role.ADMIN)
        name = name.strip()
        if not name:
            raise InvalidRequest("Enter an organization name.", code="name_invalid")
        organization = membership.organization
        organization.name = name
        organization.save(update_fields=["name", "updated_at"])
        _audit(organization.pk, actor, "organization.updated", organization)
    return membership


# --- Members ------------------------------------------------------------------


def list_members(actor: User, organization_id: int):
    """(actor membership, member queryset). Any member may list the team."""
    membership = membership_for(actor, organization_id)
    members = OrganizationMembership.objects.select_related("user").filter(
        organization_id=organization_id
    )
    return membership, members


def _target_membership(organization_id: int, membership_id: int) -> OrganizationMembership:
    target = (
        OrganizationMembership.objects.select_for_update()
        .select_related("user")
        .filter(pk=membership_id, organization_id=organization_id)
        .first()
    )
    if target is None:
        raise NotFound("Member not found.")
    return target


def change_role(
    actor: User, organization_id: int, membership_id: int, *, role: str
) -> OrganizationMembership:
    """Switch another member between ADMIN and STAFF (owner only).
    Ownership only moves through ``transfer_ownership``."""
    if role not in (Role.ADMIN, Role.STAFF):
        raise InvalidRequest("Role must be ADMIN or STAFF.", code="role_invalid")
    with transaction.atomic():
        lock_organization(organization_id)
        membership = membership_for(actor, organization_id)
        require_role(membership, Role.OWNER)
        target = _target_membership(organization_id, membership_id)
        if target.pk == membership.pk or target.role == Role.OWNER:
            raise Conflict(
                "Ownership changes only through transfer-ownership.", code="owner_role_locked"
            )
        previous = target.role
        if previous != role:
            target.role = role
            target.save(update_fields=["role", "updated_at"])
            _audit(
                organization_id,
                actor,
                "membership.role_changed",
                target,
                user_id=target.user_id,
                from_role=previous,
                to_role=role,
            )
    return target


def remove_member(actor: User, organization_id: int, membership_id: int) -> None:
    with transaction.atomic():
        lock_organization(organization_id)
        membership = membership_for(actor, organization_id)
        target = _target_membership(organization_id, membership_id)
        if target.pk == membership.pk:
            raise Conflict("Use leave to remove yourself.", code="cannot_remove_self")
        require_manages(membership, target.role)
        _audit(
            organization_id,
            actor,
            "membership.removed",
            target,
            user_id=target.user_id,
            role=target.role,
        )
        target.delete()


def leave_organization(actor: User, organization_id: int) -> None:
    with transaction.atomic():
        lock_organization(organization_id)
        membership = membership_for(actor, organization_id)
        if membership.role == Role.OWNER:
            raise Conflict(
                "Transfer ownership to another member before leaving.",
                code="owner_must_transfer",
            )
        _audit(organization_id, actor, "membership.left", membership, role=membership.role)
        membership.delete()


def transfer_ownership(
    actor: User, organization_id: int, membership_id: int
) -> OrganizationMembership:
    """Make another member the owner; the current owner becomes ADMIN.
    Both updates commit together, so there is never zero or two owners."""
    with transaction.atomic():
        lock_organization(organization_id)
        current = membership_for(actor, organization_id)
        require_role(current, Role.OWNER)
        target = _target_membership(organization_id, membership_id)
        if target.pk == current.pk:
            raise Conflict("You already own this organization.", code="already_owner")
        if not target.user.is_active or not target.user.is_email_verified:
            raise Conflict(
                "Ownership can only go to an active member with a confirmed email.",
                code="transfer_target_ineligible",
            )
        # Demote first: the one-owner unique index is checked per statement.
        current.role = Role.ADMIN
        current.save(update_fields=["role", "updated_at"])
        target.role = Role.OWNER
        target.save(update_fields=["role", "updated_at"])
        _audit(
            organization_id,
            actor,
            "organization.ownership_transferred",
            target,
            from_user_id=actor.pk,
            to_user_id=target.user_id,
        )
    return target


# --- Invitations ----------------------------------------------------------------


@dataclass(frozen=True)
class SentInvitation:
    invitation: OrganizationInvitation
    email_sent: bool


def _digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def _new_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)  # 256 bits
    return raw, _digest(raw)


def _expiry():
    return timezone.now() + timedelta(seconds=settings.ORGANIZATION_INVITATION_MAX_AGE)


def list_invitations(actor: User, organization_id: int):
    membership = membership_for(actor, organization_id)
    require_role(membership, Role.OWNER, Role.ADMIN)
    return OrganizationInvitation.objects.select_related("invited_by").filter(
        organization_id=organization_id, status=InvitationStatus.PENDING
    )


def create_invitation(
    actor: User, organization_id: int, *, email: str, role: str
) -> SentInvitation:
    email = normalize_email(email)
    try:
        validate_email(email)
    except ValidationError:
        raise InvalidRequest("Enter a valid email address.", code="email_invalid") from None
    if role not in (Role.ADMIN, Role.STAFF):
        raise InvalidRequest("Role must be ADMIN or STAFF.", code="role_invalid")

    with transaction.atomic():
        lock_organization(organization_id)
        membership = membership_for(actor, organization_id)
        require_manages(membership, role)
        if OrganizationMembership.objects.filter(
            organization_id=organization_id, user__email=email
        ).exists():
            raise Conflict("That person is already a member.", code="already_member")
        pending = OrganizationInvitation.objects.filter(
            organization_id=organization_id, email=email, status=InvitationStatus.PENDING
        ).first()
        if pending is not None:
            if pending.is_usable:
                raise Conflict(
                    "An invitation to that email is already pending. Resend it instead.",
                    code="invitation_pending",
                )
            pending.status = InvitationStatus.EXPIRED
            pending.save(update_fields=["status", "updated_at"])
        raw, digest = _new_token()
        invitation = OrganizationInvitation.objects.create(
            organization=membership.organization,
            email=email,
            role=role,
            invited_by=actor,
            token_digest=digest,
            expires_at=_expiry(),
            last_sent_at=timezone.now(),
        )
        _audit(organization_id, actor, "invitation.created", invitation, role=role)
    return SentInvitation(invitation, emails.send_invitation_email(invitation, raw))


def _managed_invitation(actor, organization_id, invitation_id) -> OrganizationInvitation:
    membership = membership_for(actor, organization_id)
    invitation = (
        OrganizationInvitation.objects.select_for_update()
        .select_related("organization")
        .filter(pk=invitation_id, organization_id=organization_id)
        .first()
    )
    if invitation is None:
        raise NotFound("Invitation not found.")
    require_manages(membership, invitation.role)
    if invitation.status != InvitationStatus.PENDING:
        raise Conflict("This invitation is no longer pending.", code="invitation_not_pending")
    return invitation


def resend_invitation(actor: User, organization_id: int, invitation_id: int) -> SentInvitation:
    """Send a fresh link. Rotates the token (the previous link stops working)
    and restarts the expiry."""
    with transaction.atomic():
        lock_organization(organization_id)
        invitation = _managed_invitation(actor, organization_id, invitation_id)
        raw, digest = _new_token()
        invitation.token_digest = digest
        invitation.expires_at = _expiry()
        invitation.last_sent_at = timezone.now()
        invitation.save(update_fields=["token_digest", "expires_at", "last_sent_at", "updated_at"])
        _audit(organization_id, actor, "invitation.resent", invitation)
    return SentInvitation(invitation, emails.send_invitation_email(invitation, raw))


def revoke_invitation(actor: User, organization_id: int, invitation_id: int) -> None:
    with transaction.atomic():
        lock_organization(organization_id)
        invitation = _managed_invitation(actor, organization_id, invitation_id)
        invitation.status = InvitationStatus.REVOKED
        invitation.revoked_at = timezone.now()
        invitation.save(update_fields=["status", "revoked_at", "updated_at"])
        _audit(organization_id, actor, "invitation.revoked", invitation, role=invitation.role)


@dataclass(frozen=True)
class Acceptance:
    membership: OrganizationMembership
    already_member: bool


def _invalid_invitation() -> InvalidRequest:
    return InvalidRequest(
        "This invitation link is invalid, expired or already used.", code="invitation_invalid"
    )


def accept_invitation(actor: User, raw_token: str) -> Acceptance:
    """Join the organization with the invited role.

    Requires a verified account whose email matches the invitation. The
    inviter must *still* be allowed to grant the role. An existing member
    keeps their current role; the invitation is just consumed.
    """
    _require_verified(actor)
    found = OrganizationInvitation.objects.filter(token_digest=_digest(raw_token)).first()
    if found is None:
        raise _invalid_invitation()

    with transaction.atomic():
        lock_organization(found.organization_id)
        invitation = (
            OrganizationInvitation.objects.select_for_update()
            .select_related("organization")
            .get(pk=found.pk)
        )
        if invitation.token_digest != _digest(raw_token) or not invitation.is_usable:
            raise _invalid_invitation()
        if invitation.email != actor.email:
            raise PermissionDenied(
                "This invitation was sent to a different email address. "
                "Log in with that address to accept it.",
                code="invitation_email_mismatch",
            )
        inviter = OrganizationMembership.objects.filter(
            organization_id=invitation.organization_id,
            user_id=invitation.invited_by_id,
            user__is_active=True,
        ).first()
        if inviter is None or invitation.role not in MANAGED_ROLES[inviter.role]:
            raise _invalid_invitation()

        existing = OrganizationMembership.objects.filter(
            organization_id=invitation.organization_id, user=actor
        ).first()
        membership = existing or OrganizationMembership.objects.create(
            organization=invitation.organization, user=actor, role=invitation.role
        )
        invitation.status = InvitationStatus.ACCEPTED
        invitation.accepted_at = timezone.now()
        invitation.accepted_by = actor
        invitation.save(update_fields=["status", "accepted_at", "accepted_by", "updated_at"])
        _audit(
            invitation.organization_id,
            actor,
            "invitation.accepted",
            invitation,
            membership_id=membership.pk,
            role=membership.role,
            already_member=existing is not None,
        )
    membership.organization = invitation.organization
    return Acceptance(membership, already_member=existing is not None)
