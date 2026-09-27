"""Organization authorization: who may do what, decided from the caller's
*current* membership row, loaded from PostgreSQL on every request.

Rules (roles are per organization; is_staff/is_superuser grant nothing here):

    action                         OWNER            ADMIN        STAFF
    view organization, members     yes              yes          yes
    update organization            yes              yes          no
    view invitations               yes              yes          no
    invite / resend / revoke       ADMIN, STAFF     STAFF        no
    remove member                  ADMIN, STAFF     STAFF        no
    change role (ADMIN<->STAFF)    yes              no           no
    transfer ownership             yes              no           no
    leave                          after transfer   yes          yes
"""

from core.exceptions import NotFound, PermissionDenied
from organizations.models import Organization, OrganizationMembership, Role

# Roles an actor may invite, and whose memberships/invitations they manage.
MANAGED_ROLES = {
    Role.OWNER: {Role.ADMIN, Role.STAFF},
    Role.ADMIN: {Role.STAFF},
    Role.STAFF: set(),
}
TEAM_MANAGERS = {Role.OWNER, Role.ADMIN}


def organization_not_found() -> NotFound:
    # Same error whether the organization doesn't exist or the caller isn't
    # a member, so ids can't be probed.
    return NotFound("Organization not found.")


def membership_for(user, organization_id: int) -> OrganizationMembership:
    """The caller's current membership, or 404."""
    membership = (
        OrganizationMembership.objects.select_related("organization")
        .filter(organization_id=organization_id, user=user, user__is_active=True)
        .first()
    )
    if membership is None:
        raise organization_not_found()
    return membership


def lock_organization(organization_id: int) -> Organization:
    """Lock the organization row. Every operation that changes memberships or
    invitations takes this lock first, so they run one at a time per
    organization and always lock in the same order (organization, then rows)."""
    organization = Organization.objects.select_for_update().filter(pk=organization_id).first()
    if organization is None:
        raise organization_not_found()
    return organization


def require_role(membership: OrganizationMembership, *roles: str) -> None:
    if membership.role not in roles:
        raise PermissionDenied("Your role in this organization does not allow this.")


def require_manages(membership: OrganizationMembership, target_role: str) -> None:
    """The actor may invite/manage people with ``target_role``."""
    if target_role not in MANAGED_ROLES[membership.role]:
        raise PermissionDenied("Your role in this organization does not allow this.")


def can_manage_team(membership: OrganizationMembership) -> bool:
    return membership.role in TEAM_MANAGERS
