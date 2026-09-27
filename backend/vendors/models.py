"""Shared vendor businesses and the people who manage them.

A VendorBusiness belongs to no organization or market: one profile will be
used to apply to many markets. Access comes only from VendorMembership, and
the OWNER membership is the ownership record (there is no owner field that
could drift from it).
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone


class Category(models.TextChoices):
    PRODUCE = "PRODUCE", "Produce"
    MEAT_DAIRY_EGGS = "MEAT_DAIRY_EGGS", "Meat, dairy & eggs"
    BAKED_GOODS = "BAKED_GOODS", "Baked goods"
    PREPARED_FOOD = "PREPARED_FOOD", "Prepared food"
    BEVERAGES = "BEVERAGES", "Beverages"
    CRAFTS = "CRAFTS", "Crafts & art"
    FLOWERS_PLANTS = "FLOWERS_PLANTS", "Flowers & plants"
    HEALTH_BEAUTY = "HEALTH_BEAUTY", "Health & beauty"
    OTHER = "OTHER", "Other"


class VendorRole(models.TextChoices):
    OWNER = "OWNER", "Owner"
    MEMBER = "MEMBER", "Member"


class VendorBusiness(models.Model):
    name = models.CharField(max_length=120)
    description = models.TextField(max_length=2000, blank=True)
    category = models.CharField(max_length=20, choices=Category.choices)
    # Business contact address, unrelated to any member's login email.
    # Normalized with accounts.models.normalize_email.
    contact_email = models.EmailField(max_length=254)
    phone = models.CharField(max_length=32, blank=True)
    website = models.URLField(max_length=200, blank=True)
    city = models.CharField(max_length=100, blank=True)
    region = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "vendor businesses"
        constraints = [
            models.CheckConstraint(condition=~Q(name=""), name="vendors_business_name_not_blank"),
            models.CheckConstraint(
                condition=Q(category__in=Category.values), name="vendors_business_category_valid"
            ),
            models.CheckConstraint(
                condition=Q(contact_email=Lower("contact_email")) & ~Q(contact_email=""),
                name="vendors_business_contact_email_normalized",
            ),
        ]

    def __str__(self):
        return self.name


class VendorMembership(models.Model):
    # PROTECT: deleting a user or business with memberships fails instead of
    # silently removing the owner or the team.
    business = models.ForeignKey(
        VendorBusiness, on_delete=models.PROTECT, related_name="memberships"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="vendor_memberships"
    )
    role = models.CharField(max_length=10, choices=VendorRole.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["business", "user"], name="vendors_membership_one_per_user"
            ),
            # At most one owner. That an owner *exists* is kept by the
            # operations in services.py (atomic creation, transfer, leave rules).
            models.UniqueConstraint(
                fields=["business"],
                condition=Q(role="OWNER"),
                name="vendors_membership_one_owner",
            ),
            models.CheckConstraint(
                condition=Q(role__in=VendorRole.values), name="vendors_membership_role_valid"
            ),
        ]

    def __str__(self):
        return f"{self.user_id} in vendor {self.business_id} as {self.role}"


class InvitationStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    ACCEPTED = "ACCEPTED", "Accepted"
    REVOKED = "REVOKED", "Revoked"
    EXPIRED = "EXPIRED", "Expired"


class VendorInvitation(models.Model):
    """Invitation to join a vendor business as MEMBER (the only invitable role)."""

    business = models.ForeignKey(
        VendorBusiness, on_delete=models.PROTECT, related_name="invitations"
    )
    email = models.EmailField(max_length=254)
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
            # One pending invitation per email per business. A stale PENDING
            # row is switched to EXPIRED inside the creating transaction.
            models.UniqueConstraint(
                "business",
                "email",
                condition=Q(status="PENDING"),
                name="vendors_invitation_one_pending_per_email",
            ),
            models.CheckConstraint(
                condition=Q(email=Lower("email")) & ~Q(email=""),
                name="vendors_invitation_email_normalized",
            ),
            models.CheckConstraint(
                condition=Q(status__in=InvitationStatus.values),
                name="vendors_invitation_status_valid",
            ),
            models.CheckConstraint(
                condition=~Q(status="ACCEPTED")
                | (Q(accepted_at__isnull=False) & Q(accepted_by__isnull=False)),
                name="vendors_invitation_accepted_has_acceptor",
            ),
        ]

    def __str__(self):
        return f"Vendor invitation {self.pk} to {self.business_id} ({self.status})"

    @property
    def is_usable(self) -> bool:
        return self.status == InvitationStatus.PENDING and self.expires_at > timezone.now()
