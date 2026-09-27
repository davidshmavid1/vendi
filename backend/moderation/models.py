"""Organization-scoped participation restrictions ("bans").

A restriction stops one account, or one vendor business, from taking part
in a single organization's markets. It never touches the account itself,
the business, or anything in other organizations. Records are append-only:
revoking fills in the revocation fields; changing a reason or duration means
revoking and issuing a new restriction.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from organizations.models import Organization
from vendors.models import VendorBusiness


class RestrictionQuerySet(models.QuerySet):
    """Status is derived at read time from revoked_at and expires_at, so
    expiry takes effect without any scheduled job."""

    def effective(self, now=None):
        now = now or timezone.now()
        return self.filter(revoked_at__isnull=True).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now)
        )

    def expired(self, now=None):
        now = now or timezone.now()
        return self.filter(revoked_at__isnull=True, expires_at__lte=now)

    def revoked(self):
        return self.filter(revoked_at__isnull=False)


class OrganizationRestriction(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="restrictions"
    )
    # Exactly one target (CHECK below).
    account = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    vendor_business = models.ForeignKey(
        VendorBusiness, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    # Internal: visible only to the organization's OWNER/ADMIN.
    reason = models.TextField(max_length=1000)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    revocation_note = models.TextField(max_length=1000, blank=True)

    objects = RestrictionQuerySet.as_manager()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(Q(account__isnull=False) & Q(vendor_business__isnull=True))
                | (Q(account__isnull=True) & Q(vendor_business__isnull=False)),
                name="moderation_restriction_exactly_one_target",
            ),
            models.CheckConstraint(
                condition=~Q(reason=""), name="moderation_restriction_reason_not_blank"
            ),
            models.CheckConstraint(
                condition=(Q(revoked_at__isnull=True) & Q(revoked_by__isnull=True))
                | (Q(revoked_at__isnull=False) & Q(revoked_by__isnull=False)),
                name="moderation_restriction_revocation_complete",
            ),
            models.CheckConstraint(
                condition=Q(expires_at__isnull=True) | Q(expires_at__gt=models.F("created_at")),
                name="moderation_restriction_expires_after_creation",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "account"], name="moderation_org_account_idx"),
            models.Index(
                fields=["organization", "vendor_business"], name="moderation_org_vendor_idx"
            ),
        ]

    def __str__(self):
        return f"Restriction {self.pk} in {self.organization_id}"

    @property
    def target_type(self) -> str:
        return "account" if self.account_id is not None else "vendor_business"

    def status(self, now=None) -> str:
        now = now or timezone.now()
        if self.revoked_at is not None:
            return "revoked"
        if self.expires_at is not None and self.expires_at <= now:
            return "expired"
        return "effective"
