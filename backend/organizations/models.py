"""Organizations, their team memberships, invitations and audit trail.

Authority inside an organization comes only from OrganizationMembership.
There is no owner field on Organization: the OWNER membership *is* the
ownership record, so the two cannot drift apart.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone


class Role(models.TextChoices):
    OWNER = "OWNER", "Owner"
    ADMIN = "ADMIN", "Admin"
    STAFF = "STAFF", "Staff"


class Organization(models.Model):
    name = models.CharField(max_length=120)
    # Immutable in this phase; used in future public URLs.
    slug = models.SlugField(max_length=60, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(slug__regex=r"^[a-z0-9]+(-[a-z0-9]+)*$"),
                name="organizations_organization_slug_format",
            ),
            models.CheckConstraint(
                condition=~Q(name=""), name="organizations_organization_name_not_blank"
            ),
        ]

    def __str__(self):
        return self.name


class OrganizationMembership(models.Model):
    # PROTECT on both sides: deleting a user or organization that still has
    # memberships fails instead of silently removing the team (or the owner).
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="memberships"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="organization_memberships",
    )
    role = models.CharField(max_length=10, choices=Role.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "user"], name="organizations_membership_one_per_user"
            ),
            # At most one owner. That an owner *exists* is kept by the
            # operations in services.py (atomic creation, transfer, leave rules).
            models.UniqueConstraint(
                fields=["organization"],
                condition=Q(role="OWNER"),
                name="organizations_membership_one_owner",
            ),
            models.CheckConstraint(
                condition=Q(role__in=Role.values), name="organizations_membership_role_valid"
            ),
        ]

    def __str__(self):
        return f"{self.user_id} in {self.organization_id} as {self.role}"


class InvitationStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    ACCEPTED = "ACCEPTED", "Accepted"
    REVOKED = "REVOKED", "Revoked"
    EXPIRED = "EXPIRED", "Expired"


class OrganizationInvitation(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="invitations"
    )
    # Normalized with accounts.models.normalize_email.
    email = models.EmailField(max_length=254)
    role = models.CharField(max_length=10, choices=Role.choices)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    # SHA-256 of the emailed token; the token itself is never stored.
    token_digest = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    status = models.CharField(
        max_length=10, choices=InvitationStatus.choices, default=InvitationStatus.PENDING
    )
    last_sent_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # One pending invitation per email per organization. Expiry is a
            # time comparison, so a stale PENDING row is switched to EXPIRED
            # (inside the creating transaction) before a new one is added.
            models.UniqueConstraint(
                "organization",
                "email",
                condition=Q(status="PENDING"),
                name="organizations_invitation_one_pending_per_email",
            ),
            models.CheckConstraint(
                condition=Q(role__in=[Role.ADMIN, Role.STAFF]),
                name="organizations_invitation_role_not_owner",
            ),
            models.CheckConstraint(
                condition=Q(email=Lower("email")) & ~Q(email=""),
                name="organizations_invitation_email_normalized",
            ),
            models.CheckConstraint(
                condition=Q(status__in=InvitationStatus.values),
                name="organizations_invitation_status_valid",
            ),
            models.CheckConstraint(
                condition=~Q(status="ACCEPTED")
                | (Q(accepted_at__isnull=False) & Q(accepted_by__isnull=False)),
                name="organizations_invitation_accepted_has_acceptor",
            ),
        ]

    def __str__(self):
        return f"Invitation {self.pk} to {self.organization_id} ({self.status})"

    @property
    def is_usable(self) -> bool:
        return self.status == InvitationStatus.PENDING and self.expires_at > timezone.now()


class OrganizationAuditEvent(models.Model):
    """Append-only record of security-sensitive team changes, written in the
    same transaction as the change. Holds ids and roles, never tokens or
    email addresses."""

    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="audit_events"
    )
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    action = models.CharField(max_length=50)
    subject_type = models.CharField(max_length=30)
    subject_id = models.BigIntegerField()
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["organization", "created_at"], name="org_audit_org_created_idx")
        ]

    def __str__(self):
        return f"{self.action} on {self.subject_type} {self.subject_id}"
