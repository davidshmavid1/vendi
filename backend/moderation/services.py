"""Issuing, listing and revoking participation restrictions.

Only an organization's OWNER or ADMIN may moderate, checked against their
current membership on every call. Creation and revocation lock the
organization row, so two moderators can't create overlapping effective
restrictions for the same target at the same time.
"""

from datetime import datetime

from django.db import transaction
from django.utils import timezone

from accounts.models import User
from core.exceptions import Conflict, InvalidRequest, NotFound
from moderation.models import OrganizationRestriction
from organizations.models import OrganizationAuditEvent, Role
from organizations.permissions import lock_organization, membership_for, require_role
from vendors.models import VendorBusiness

REASON_MAX_LENGTH = 1000
STATUSES = ("effective", "expired", "revoked")
TARGET_TYPES = ("account", "vendor_business")


def _require_moderator(actor: User, organization_id: int):
    membership = membership_for(actor, organization_id)
    require_role(membership, Role.OWNER, Role.ADMIN)
    return membership


def _audit(restriction: OrganizationRestriction, actor: User, action: str) -> None:
    # Target ids only; the internal reason and notes stay out of the audit trail.
    OrganizationAuditEvent.objects.create(
        organization_id=restriction.organization_id,
        actor=actor,
        action=action,
        subject_type="organizationrestriction",
        subject_id=restriction.pk,
        details={
            "target_type": restriction.target_type,
            "target_id": restriction.account_id or restriction.vendor_business_id,
        },
    )


def _resolve_target(account_id: int | None, vendor_business_id: int | None) -> dict:
    if (account_id is None) == (vendor_business_id is None):
        raise InvalidRequest(
            "Give exactly one of account_id or vendor_business_id.", code="target_invalid"
        )
    if account_id is not None:
        account = User.objects.filter(pk=account_id).first()
        if account is None:
            raise InvalidRequest("No such account.", code="target_not_found")
        return {"account": account}
    business = VendorBusiness.objects.filter(pk=vendor_business_id).first()
    if business is None:
        raise InvalidRequest("No such vendor business.", code="target_not_found")
    return {"vendor_business": business}


def _clean_reason(reason: str) -> str:
    reason = (reason or "").strip()
    if not reason or len(reason) > REASON_MAX_LENGTH:
        raise InvalidRequest(
            f"Enter a reason of 1 to {REASON_MAX_LENGTH} characters.", code="reason_invalid"
        )
    return reason


def _clean_expiry(expires_at: datetime | None, now: datetime) -> datetime | None:
    if expires_at is None:
        return None
    if timezone.is_naive(expires_at):
        raise InvalidRequest(
            "expires_at must include a timezone, e.g. 2026-12-31T23:59:00Z.",
            code="expires_at_invalid",
        )
    if expires_at <= now:
        raise InvalidRequest("expires_at must be in the future.", code="expires_at_invalid")
    return expires_at


def create_restriction(
    actor: User,
    organization_id: int,
    *,
    account_id: int | None = None,
    vendor_business_id: int | None = None,
    reason: str,
    expires_at: datetime | None = None,
) -> OrganizationRestriction:
    reason = _clean_reason(reason)
    with transaction.atomic():
        lock_organization(organization_id)
        _require_moderator(actor, organization_id)
        target = _resolve_target(account_id, vendor_business_id)
        now = timezone.now()
        expires_at = _clean_expiry(expires_at, now)
        existing = (
            OrganizationRestriction.objects.effective(now)
            .filter(organization_id=organization_id, **target)
            .first()
        )
        if existing is not None:
            raise Conflict(
                "This target already has an effective restriction. Revoke it to issue a new one.",
                code="already_restricted",
                details=[{"restriction_id": existing.pk}],
            )
        restriction = OrganizationRestriction.objects.create(
            organization_id=organization_id,
            reason=reason,
            created_by=actor,
            expires_at=expires_at,
            **target,
        )
        _audit(restriction, actor, "restriction.created")
    return restriction


def list_restrictions(
    actor: User, organization_id: int, *, status: str | None = None, target_type: str | None = None
):
    _require_moderator(actor, organization_id)
    restrictions = OrganizationRestriction.objects.filter(organization_id=organization_id)
    if status == "effective":
        restrictions = restrictions.effective()
    elif status == "expired":
        restrictions = restrictions.expired()
    elif status == "revoked":
        restrictions = restrictions.revoked()
    if target_type == "account":
        restrictions = restrictions.filter(account__isnull=False)
    elif target_type == "vendor_business":
        restrictions = restrictions.filter(vendor_business__isnull=False)
    return restrictions


def _restriction_in(organization_id: int, restriction_id: int, *, lock=False):
    queryset = OrganizationRestriction.objects.filter(
        pk=restriction_id, organization_id=organization_id
    )
    restriction = (queryset.select_for_update() if lock else queryset).first()
    if restriction is None:
        raise NotFound("Restriction not found.")
    return restriction


def get_restriction(actor: User, organization_id: int, restriction_id: int):
    _require_moderator(actor, organization_id)
    return _restriction_in(organization_id, restriction_id)


def revoke_restriction(
    actor: User, organization_id: int, restriction_id: int, *, note: str = ""
) -> OrganizationRestriction:
    """End an effective restriction. Already revoked or expired restrictions
    are left exactly as they are (409), so history is never rewritten."""
    note = (note or "").strip()
    if len(note) > REASON_MAX_LENGTH:
        raise InvalidRequest("The note is too long.", code="note_invalid")
    with transaction.atomic():
        lock_organization(organization_id)
        _require_moderator(actor, organization_id)
        restriction = _restriction_in(organization_id, restriction_id, lock=True)
        status = restriction.status()
        if status == "revoked":
            raise Conflict("This restriction was already revoked.", code="already_revoked")
        if status == "expired":
            raise Conflict("This restriction has already expired.", code="restriction_expired")
        restriction.revoked_at = timezone.now()
        restriction.revoked_by = actor
        restriction.revocation_note = note
        restriction.save(update_fields=["revoked_at", "revoked_by", "revocation_note"])
        _audit(restriction, actor, "restriction.revoked")
    return restriction
