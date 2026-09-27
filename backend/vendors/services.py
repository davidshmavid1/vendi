"""Vendor business and membership operations.

Each operation takes the acting user, re-reads their membership
(vendors/permissions.py) and checks it. Operations that change memberships
or invitations lock the business row first and re-check the actor inside
that transaction. Emails are sent after commit.
"""

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator, validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import User, normalize_email
from core.exceptions import Conflict, InvalidRequest, NotFound, PermissionDenied
from vendors import emails
from vendors.models import (
    Category,
    InvitationStatus,
    VendorBusiness,
    VendorInvitation,
    VendorMembership,
    VendorRole,
)
from vendors.permissions import lock_business, membership_for, require_owner

# The only profile fields a client may set. Ownership, memberships,
# timestamps and ids are never taken from input.
EDITABLE_FIELDS = (
    "name",
    "description",
    "category",
    "contact_email",
    "phone",
    "website",
    "city",
    "region",
)
PHONE_PATTERN = re.compile(r"[0-9+()\-. ]{7,32}")
_validate_url = URLValidator(schemes=["http", "https"])


def _require_verified(user: User) -> None:
    if not user.is_email_verified:
        raise PermissionDenied(
            "Confirm your email address before doing this.", code="email_not_verified"
        )


# --- Profile ------------------------------------------------------------------


def _clean_profile(values: dict) -> dict:
    """Validate and normalize editable fields present in ``values``."""
    cleaned = {}
    for field, raw in values.items():
        if field not in EDITABLE_FIELDS:
            raise InvalidRequest(f"{field} cannot be set.", code="field_not_editable")
        value = (raw or "").strip()
        if field == "name" and not value:
            raise InvalidRequest("Enter a business name.", code="name_invalid")
        if field == "category" and value not in Category.values:
            raise InvalidRequest("Choose a listed category.", code="category_invalid")
        if field == "contact_email":
            value = normalize_email(value)
            try:
                validate_email(value)
            except ValidationError:
                raise InvalidRequest(
                    "Enter a valid contact email.", code="contact_email_invalid"
                ) from None
        if field == "phone" and value and not PHONE_PATTERN.fullmatch(value):
            raise InvalidRequest("Enter a valid phone number.", code="phone_invalid")
        if field == "website" and value:
            try:
                _validate_url(value)
            except ValidationError:
                raise InvalidRequest(
                    "Enter a full http:// or https:// address.", code="website_invalid"
                ) from None
        cleaned[field] = value
    return cleaned


def create_business(actor: User, **values) -> VendorMembership:
    """Create a vendor business with ``actor`` as its owner, atomically."""
    _require_verified(actor)
    cleaned = _clean_profile(values)
    with transaction.atomic():
        business = VendorBusiness.objects.create(**cleaned)
        return VendorMembership.objects.create(business=business, user=actor, role=VendorRole.OWNER)


def list_memberships(actor: User):
    return VendorMembership.objects.select_related("business").filter(user=actor)


def get_business(actor: User, business_id: int) -> VendorMembership:
    return membership_for(actor, business_id)


def update_business(actor: User, business_id: int, **values) -> VendorMembership:
    cleaned = _clean_profile(values)
    with transaction.atomic():
        lock_business(business_id)
        membership = membership_for(actor, business_id)
        require_owner(membership)
        business = membership.business
        for field, value in cleaned.items():
            setattr(business, field, value)
        if cleaned:
            business.save(update_fields=[*cleaned, "updated_at"])
    return membership


# --- Members ------------------------------------------------------------------


def list_members(actor: User, business_id: int):
    membership = membership_for(actor, business_id)
    members = VendorMembership.objects.select_related("user").filter(business_id=business_id)
    return membership, members


def _target_membership(business_id: int, membership_id: int) -> VendorMembership:
    target = (
        VendorMembership.objects.select_for_update()
        .select_related("user")
        .filter(pk=membership_id, business_id=business_id)
        .first()
    )
    if target is None:
        raise NotFound("Member not found.")
    return target


def remove_member(actor: User, business_id: int, membership_id: int) -> None:
    with transaction.atomic():
        lock_business(business_id)
        membership = membership_for(actor, business_id)
        require_owner(membership)
        target = _target_membership(business_id, membership_id)
        if target.role == VendorRole.OWNER:
            raise Conflict(
                "The owner can't be removed. Transfer ownership first.",
                code="owner_must_transfer",
            )
        target.delete()


def leave_business(actor: User, business_id: int) -> None:
    with transaction.atomic():
        lock_business(business_id)
        membership = membership_for(actor, business_id)
        if membership.role == VendorRole.OWNER:
            raise Conflict(
                "Transfer ownership to another member before leaving.",
                code="owner_must_transfer",
            )
        membership.delete()


def transfer_ownership(actor: User, business_id: int, membership_id: int) -> VendorMembership:
    """Make another member the owner; the current owner becomes MEMBER.
    Both updates commit together, so there is never zero or two owners."""
    with transaction.atomic():
        lock_business(business_id)
        current = membership_for(actor, business_id)
        require_owner(current)
        target = _target_membership(business_id, membership_id)
        if target.pk == current.pk:
            raise Conflict("You already own this business.", code="already_owner")
        if not target.user.is_active or not target.user.is_email_verified:
            raise Conflict(
                "Ownership can only go to an active member with a confirmed email.",
                code="transfer_target_ineligible",
            )
        # Demote first: the one-owner unique index is checked per statement.
        current.role = VendorRole.MEMBER
        current.save(update_fields=["role", "updated_at"])
        target.role = VendorRole.OWNER
        target.save(update_fields=["role", "updated_at"])
    return target


# --- Invitations ------------------------------------------------------------------


@dataclass(frozen=True)
class SentInvitation:
    invitation: VendorInvitation
    email_sent: bool


def _digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def _new_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)  # 256 bits
    return raw, _digest(raw)


def _expiry():
    return timezone.now() + timedelta(seconds=settings.VENDOR_INVITATION_MAX_AGE)


def list_invitations(actor: User, business_id: int):
    membership = membership_for(actor, business_id)
    require_owner(membership)
    return VendorInvitation.objects.filter(business_id=business_id, status=InvitationStatus.PENDING)


def create_invitation(actor: User, business_id: int, *, email: str) -> SentInvitation:
    email = normalize_email(email)
    try:
        validate_email(email)
    except ValidationError:
        raise InvalidRequest("Enter a valid email address.", code="email_invalid") from None

    with transaction.atomic():
        business = lock_business(business_id)
        membership = membership_for(actor, business_id)
        require_owner(membership)
        if VendorMembership.objects.filter(business_id=business_id, user__email=email).exists():
            raise Conflict("That person is already a member.", code="already_member")
        pending = VendorInvitation.objects.filter(
            business_id=business_id, email=email, status=InvitationStatus.PENDING
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
        invitation = VendorInvitation.objects.create(
            business=business,
            email=email,
            invited_by=actor,
            token_digest=digest,
            expires_at=_expiry(),
            last_sent_at=timezone.now(),
        )
    return SentInvitation(invitation, emails.send_invitation_email(invitation, raw))


def _owned_pending_invitation(actor, business_id, invitation_id) -> VendorInvitation:
    membership = membership_for(actor, business_id)
    require_owner(membership)
    invitation = (
        VendorInvitation.objects.select_for_update()
        .select_related("business")
        .filter(pk=invitation_id, business_id=business_id)
        .first()
    )
    if invitation is None:
        raise NotFound("Invitation not found.")
    if invitation.status != InvitationStatus.PENDING:
        raise Conflict("This invitation is no longer pending.", code="invitation_not_pending")
    return invitation


def resend_invitation(actor: User, business_id: int, invitation_id: int) -> SentInvitation:
    """Send a fresh link. Rotates the token (the previous link stops working)
    and restarts the expiry."""
    with transaction.atomic():
        lock_business(business_id)
        invitation = _owned_pending_invitation(actor, business_id, invitation_id)
        raw, digest = _new_token()
        invitation.token_digest = digest
        invitation.expires_at = _expiry()
        invitation.last_sent_at = timezone.now()
        invitation.save(update_fields=["token_digest", "expires_at", "last_sent_at", "updated_at"])
    return SentInvitation(invitation, emails.send_invitation_email(invitation, raw))


def revoke_invitation(actor: User, business_id: int, invitation_id: int) -> None:
    with transaction.atomic():
        lock_business(business_id)
        invitation = _owned_pending_invitation(actor, business_id, invitation_id)
        invitation.status = InvitationStatus.REVOKED
        invitation.revoked_at = timezone.now()
        invitation.save(update_fields=["status", "revoked_at", "updated_at"])


@dataclass(frozen=True)
class Acceptance:
    membership: VendorMembership
    already_member: bool


def _invalid_invitation() -> InvalidRequest:
    return InvalidRequest(
        "This invitation link is invalid, expired or already used.", code="invitation_invalid"
    )


def accept_invitation(actor: User, raw_token: str) -> Acceptance:
    """Join the business as MEMBER.

    Requires a verified account whose email matches the invitation; the token
    alone authorizes nothing. The inviter must still be the owner. An existing
    member keeps their current role; the invitation is just consumed.
    """
    _require_verified(actor)
    found = VendorInvitation.objects.filter(token_digest=_digest(raw_token)).first()
    if found is None:
        raise _invalid_invitation()

    with transaction.atomic():
        lock_business(found.business_id)
        invitation = (
            VendorInvitation.objects.select_for_update().select_related("business").get(pk=found.pk)
        )
        if invitation.token_digest != _digest(raw_token) or not invitation.is_usable:
            raise _invalid_invitation()
        if invitation.email != actor.email:
            raise PermissionDenied(
                "This invitation was sent to a different email address. "
                "Log in with that address to accept it.",
                code="invitation_email_mismatch",
            )
        inviter_is_owner = VendorMembership.objects.filter(
            business_id=invitation.business_id,
            user_id=invitation.invited_by_id,
            user__is_active=True,
            role=VendorRole.OWNER,
        ).exists()
        if not inviter_is_owner:
            raise _invalid_invitation()

        existing = VendorMembership.objects.filter(
            business_id=invitation.business_id, user=actor
        ).first()
        try:
            membership = existing or VendorMembership.objects.create(
                business=invitation.business, user=actor, role=VendorRole.MEMBER
            )
        except IntegrityError:  # pragma: no cover - prevented by the business lock
            raise Conflict("You are already a member.", code="already_member") from None
        invitation.status = InvitationStatus.ACCEPTED
        invitation.accepted_at = timezone.now()
        invitation.accepted_by = actor
        invitation.save(update_fields=["status", "accepted_at", "accepted_by", "updated_at"])
    membership.business = invitation.business
    return Acceptance(membership, already_member=existing is not None)
