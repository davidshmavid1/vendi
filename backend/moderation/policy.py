"""Participation policy: may this account (and, if given, this vendor
business) take part in an organization's markets?

This is *only* the moderation check. It is not authentication and not vendor
authorization: callers must separately verify that ``account`` is allowed to
act for ``vendor_business`` (vendors.permissions.membership_for) before
calling it.

Entry points that must call ``ensure_can_participate`` inside their
transaction: submitting a market application (applications.services.submit;
approval re-checks with ``is_restricted``), and in future phases creating a
reservation or booking (Phases 11-12), and starting a new payment for either
(Phase 13). Reading one's own records, cancelling, and refunds must *not*
call it.
"""

from django.db.models import Q
from django.utils import timezone

from core.exceptions import PermissionDenied
from moderation.models import OrganizationRestriction


class ParticipationRestricted(PermissionDenied):
    """Generic on purpose: never says why, or whether the account or the
    business matched."""

    default_code = "participation_restricted"


def is_restricted(organization_id: int, *, account, vendor_business=None, now=None) -> bool:
    target = Q(account=account)
    if vendor_business is not None:
        target |= Q(vendor_business=vendor_business)
    return (
        OrganizationRestriction.objects.effective(now or timezone.now())
        .filter(target, organization_id=organization_id)
        .exists()
    )


def ensure_can_participate(organization_id: int, *, account, vendor_business=None) -> None:
    """Raise ParticipationRestricted if the acting account, or the vendor
    business it acts for, has an effective restriction in this organization."""
    if is_restricted(organization_id, account=account, vendor_business=vendor_business):
        raise ParticipationRestricted(
            "You can't take part in this organization's markets right now."
        )
