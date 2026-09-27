"""Stall reservations: time-limited holds on a date's stall offers.

A Reservation claims one StallOffer for one vendor business, under that
business's approved application for the same date. It copies the offer's
price and currency when created, so later price edits never change it.

Lifecycle (see reservations.services):

    HELD ──(expires_at passes)──> EXPIRED
      │ └──(owner releases)─────> RELEASED
      └──(confirm_hold, Phase 13)> CONFIRMED

A HELD row whose ``expires_at`` has passed no longer blocks anything: every
operation that could be affected first switches such rows to EXPIRED under
the date's lock, so correctness never depends on a scheduled job.
Reservations are never deleted.
"""

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from applications.models import Application
from layouts.models import StallOffer
from layouts.money import MAX_PRICE_MINOR, SUPPORTED_CURRENCIES
from markets.models import EventOccurrence
from vendors.models import VendorBusiness


class ReservationStatus(models.TextChoices):
    HELD = "HELD", "Held"
    EXPIRED = "EXPIRED", "Expired"
    RELEASED = "RELEASED", "Released"
    CONFIRMED = "CONFIRMED", "Confirmed"


# Statuses that occupy inventory. HELD rows past ``expires_at`` are switched
# to EXPIRED before any new claim, so this list never depends on the clock.
OCCUPYING = (ReservationStatus.HELD, ReservationStatus.CONFIRMED)


class ReservationQuerySet(models.QuerySet):
    def occupying(self, now):
        """Reservations that block their stall at ``now``: confirmed ones, and
        holds that haven't lapsed (even if not yet switched to EXPIRED)."""
        return self.filter(
            Q(status=ReservationStatus.CONFIRMED)
            | Q(status=ReservationStatus.HELD, expires_at__gt=now)
        )

    def lapsed(self, now):
        """Holds whose time is up but that are still marked HELD."""
        return self.filter(status=ReservationStatus.HELD, expires_at__lte=now)


class Reservation(models.Model):
    offer = models.ForeignKey(StallOffer, on_delete=models.PROTECT, related_name="reservations")
    # Copies of the offer's and application's date and business, so the
    # composite foreign keys (migration 0001) and the partial unique indexes
    # below can be enforced by PostgreSQL.
    occurrence = models.ForeignKey(
        EventOccurrence, on_delete=models.PROTECT, related_name="reservations"
    )
    application = models.ForeignKey(
        Application, on_delete=models.PROTECT, related_name="reservations"
    )
    vendor_business = models.ForeignKey(
        VendorBusiness, on_delete=models.PROTECT, related_name="reservations"
    )
    held_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    status = models.CharField(
        max_length=10, choices=ReservationStatus.choices, default=ReservationStatus.HELD
    )
    # Snapshot of the offer when the hold was taken (integer minor units).
    price_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3)
    expires_at = models.DateTimeField()
    # Client-chosen idempotency key, unique per account and business.
    request_key = models.CharField(max_length=64)
    expired_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    objects = ReservationQuerySet.as_manager()

    class Meta:
        constraints = [
            # One occupying reservation per offer (stall on a date).
            models.UniqueConstraint(
                fields=["offer"],
                condition=Q(status__in=OCCUPYING),
                name="reservations_one_occupying_per_offer",
            ),
            # One occupying reservation per business per date.
            models.UniqueConstraint(
                fields=["vendor_business", "occurrence"],
                condition=Q(status__in=OCCUPYING),
                name="reservations_one_occupying_per_business_date",
            ),
            models.UniqueConstraint(
                fields=["held_by", "vendor_business", "request_key"],
                name="reservations_request_key_unique",
            ),
            models.CheckConstraint(
                condition=Q(status__in=ReservationStatus.values),
                name="reservations_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(price_minor__gte=0, price_minor__lte=MAX_PRICE_MINOR),
                name="reservations_price_range",
            ),
            models.CheckConstraint(
                condition=Q(currency__in=SUPPORTED_CURRENCIES),
                name="reservations_currency_supported",
            ),
            models.CheckConstraint(
                condition=Q(expires_at__gt=F("created_at")),
                name="reservations_expires_after_created",
            ),
            models.CheckConstraint(
                condition=~Q(request_key=""), name="reservations_request_key_not_blank"
            ),
            # Each end state carries exactly its own timestamp.
            models.CheckConstraint(
                condition=(
                    Q(status="EXPIRED", expired_at__isnull=False)
                    | (~Q(status="EXPIRED") & Q(expired_at__isnull=True))
                )
                & (
                    Q(status="RELEASED", released_at__isnull=False, released_by__isnull=False)
                    | (
                        ~Q(status="RELEASED")
                        & Q(released_at__isnull=True, released_by__isnull=True)
                    )
                )
                & (
                    Q(status="CONFIRMED", confirmed_at__isnull=False)
                    | (~Q(status="CONFIRMED") & Q(confirmed_at__isnull=True))
                ),
                name="reservations_lifecycle_fields_match",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "expires_at"], name="reservations_status_exp_idx"),
        ]

    def __str__(self):
        return f"Reservation {self.pk} of offer {self.offer_id} ({self.status})"

    def status_at(self, now) -> str:
        """The status to show: a lapsed hold reads as EXPIRED even before the
        row itself is switched."""
        if self.status == ReservationStatus.HELD and self.expires_at <= now:
            return ReservationStatus.EXPIRED
        return self.status
