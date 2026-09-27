"""Markets, their scheduled event dates, and weekly recurrence series.

A Market is the ongoing market or popup concept, owned by one organization.
An EventOccurrence is one concrete dated event; later phases (applications,
stalls, bookings) reference occurrences, so they are never deleted.
A RecurrenceSeries records how a batch of occurrences was generated.
"""

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from organizations.models import Organization


class MarketType(models.TextChoices):
    FARMERS_MARKET = "FARMERS_MARKET", "Farmers market"
    POPUP = "POPUP", "Popup"


class MarketStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    PUBLISHED = "PUBLISHED", "Published"
    ARCHIVED = "ARCHIVED", "Archived"


# Longest vendor cancellation cutoff an organizer can set (one year).
MAX_CANCELLATION_CUTOFF_HOURS = 24 * 365


class Market(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="markets")
    name = models.CharField(max_length=120)
    description = models.TextField(max_length=5000, blank=True)
    market_type = models.CharField(max_length=20, choices=MarketType.choices)
    venue_name = models.CharField(max_length=200, blank=True)
    address_line1 = models.CharField(max_length=200, blank=True)
    address_line2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100, blank=True)
    region = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    # ISO 3166-1 alpha-2, uppercase (e.g. "US").
    country = models.CharField(max_length=2, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    # IANA name, e.g. "America/Chicago". Local times of occurrences and
    # recurrence series are interpreted in this zone.
    timezone = models.CharField(max_length=64)
    status = models.CharField(
        max_length=10, choices=MarketStatus.choices, default=MarketStatus.DRAFT
    )
    published_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    # Vendor cancellation policy (Phase 14): vendors may cancel a booking
    # themselves, for a refund of what they paid minus Vendi's fee, until this
    # many hours before the date starts. Null: vendors can't cancel on their
    # own (they contact the organizer). New holds snapshot it; editing it never
    # changes existing bookings.
    vendor_cancellation_cutoff_hours = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=~Q(name=""), name="markets_market_name_not_blank"),
            models.CheckConstraint(
                condition=Q(vendor_cancellation_cutoff_hours__lte=MAX_CANCELLATION_CUTOFF_HOURS),
                name="markets_market_cancellation_cutoff_range",
            ),
            models.CheckConstraint(
                condition=Q(market_type__in=MarketType.values), name="markets_market_type_valid"
            ),
            models.CheckConstraint(
                condition=Q(status__in=MarketStatus.values), name="markets_market_status_valid"
            ),
            models.CheckConstraint(
                condition=Q(latitude__isnull=True, longitude__isnull=True)
                | Q(
                    latitude__gte=-90,
                    latitude__lte=90,
                    longitude__gte=-180,
                    longitude__lte=180,
                ),
                name="markets_market_coordinates_valid",
            ),
            models.CheckConstraint(
                condition=Q(country="") | Q(country__regex=r"^[A-Z]{2}$"),
                name="markets_market_country_format",
            ),
            # A published market always has a usable public venue.
            models.CheckConstraint(
                condition=~Q(status="PUBLISHED")
                | (~Q(venue_name="") & ~Q(address_line1="") & ~Q(city="") & ~Q(country="")),
                name="markets_market_published_has_venue",
            ),
        ]
        indexes = [
            # Discovery's bounding-box prefilter (map areas and nearby
            # searches) only ever looks at published markets.
            models.Index(
                fields=["latitude", "longitude"],
                condition=Q(status="PUBLISHED"),
                name="markets_published_coords_idx",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None


class RecurrenceSeries(models.Model):
    """A weekly schedule that generated concrete occurrences. Kept so each
    generated occurrence can be traced to its schedule; never regenerated
    in place or edited in this phase."""

    market = models.ForeignKey(Market, on_delete=models.PROTECT, related_name="series")
    interval_weeks = models.PositiveSmallIntegerField()
    # ISO weekdays, 1 = Monday ... 7 = Sunday, sorted and unique.
    weekdays = ArrayField(models.PositiveSmallIntegerField())
    start_date = models.DateField()
    end_date = models.DateField()
    local_start_time = models.TimeField()
    local_end_time = models.TimeField()
    # The market's timezone when the series was generated.
    timezone = models.CharField(max_length=64)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "recurrence series"
        constraints = [
            # Same definition = same series: repeating a request is a no-op.
            models.UniqueConstraint(
                fields=[
                    "market",
                    "interval_weeks",
                    "weekdays",
                    "start_date",
                    "end_date",
                    "local_start_time",
                    "local_end_time",
                ],
                name="markets_series_unique_definition",
            ),
            models.CheckConstraint(
                condition=Q(interval_weeks__gte=1, interval_weeks__lte=12),
                name="markets_series_interval_range",
            ),
            models.CheckConstraint(
                condition=Q(end_date__gte=F("start_date")), name="markets_series_date_order"
            ),
            models.CheckConstraint(
                condition=Q(local_end_time__gt=F("local_start_time")),
                name="markets_series_time_order",
            ),
        ]

    def __str__(self):
        return f"Series {self.pk} for market {self.market_id}"


class OccurrenceStatus(models.TextChoices):
    SCHEDULED = "SCHEDULED", "Scheduled"
    CANCELLED = "CANCELLED", "Cancelled"


class EventOccurrenceQuerySet(models.QuerySet):
    def upcoming(self, now=None):
        """Not yet finished (ends in the future), in start order."""
        return self.filter(ends_at__gt=now or timezone.now()).order_by("starts_at", "pk")

    def scheduled(self):
        return self.filter(status=OccurrenceStatus.SCHEDULED)


class EventOccurrence(models.Model):
    market = models.ForeignKey(Market, on_delete=models.PROTECT, related_name="occurrences")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    status = models.CharField(
        max_length=10, choices=OccurrenceStatus.choices, default=OccurrenceStatus.SCHEDULED
    )
    cancellation_message = models.TextField(max_length=500, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    # Set for generated occurrences: the series and the start it was
    # generated for. The slot stays fixed even if the occurrence is later
    # moved, so regenerating never recreates or resets it.
    series = models.ForeignKey(
        RecurrenceSeries,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="occurrences",
    )
    series_slot_start = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = EventOccurrenceQuerySet.as_manager()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__gt=F("starts_at")), name="markets_occurrence_ends_after_start"
            ),
            models.UniqueConstraint(
                fields=["market", "starts_at"], name="markets_occurrence_unique_start"
            ),
            models.UniqueConstraint(
                fields=["series", "series_slot_start"], name="markets_occurrence_unique_slot"
            ),
            models.CheckConstraint(
                condition=Q(status__in=OccurrenceStatus.values),
                name="markets_occurrence_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(series__isnull=True, series_slot_start__isnull=True)
                | Q(series__isnull=False, series_slot_start__isnull=False),
                name="markets_occurrence_series_slot_together",
            ),
            models.CheckConstraint(
                condition=Q(status="SCHEDULED", cancelled_at__isnull=True)
                | Q(status="CANCELLED", cancelled_at__isnull=False),
                name="markets_occurrence_cancelled_at_matches",
            ),
        ]
        indexes = [
            models.Index(fields=["market", "ends_at"], name="markets_occ_market_ends_idx"),
        ]

    def __str__(self):
        return f"Occurrence {self.pk} of market {self.market_id}"
