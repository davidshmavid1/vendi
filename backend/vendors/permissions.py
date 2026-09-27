"""Vendor business authorization, decided from the caller's *current*
VendorMembership row on every request.

    action                              OWNER   MEMBER
    view profile, member directory      yes     yes (no emails)
    update profile                      yes     no
    invite / list / resend / revoke     yes     no
    remove a MEMBER                     yes     no
    transfer ownership                  yes     no
    leave                               after transfer   yes

Organization memberships, is_staff and is_superuser grant nothing here.
"""

from core.exceptions import NotFound, PermissionDenied
from vendors.models import VendorBusiness, VendorMembership, VendorRole


def business_not_found() -> NotFound:
    # Same error whether the business doesn't exist or the caller isn't a
    # member, so ids can't be probed.
    return NotFound("Vendor business not found.")


def membership_for(user, business_id: int) -> VendorMembership:
    membership = (
        VendorMembership.objects.select_related("business")
        .filter(business_id=business_id, user=user, user__is_active=True)
        .first()
    )
    if membership is None:
        raise business_not_found()
    return membership


def lock_business(business_id: int) -> VendorBusiness:
    """Lock the business row. Every operation that changes memberships or
    invitations takes this lock first (business, then rows), so they run one
    at a time per business."""
    business = VendorBusiness.objects.select_for_update().filter(pk=business_id).first()
    if business is None:
        raise business_not_found()
    return business


def require_owner(membership: VendorMembership) -> None:
    if membership.role != VendorRole.OWNER:
        raise PermissionDenied("Only the business owner can do this.")


def is_owner(membership: VendorMembership) -> bool:
    return membership.role == VendorRole.OWNER
