"""Stall layouts and per-date pricing.

- LayoutVersion: a market's physical plan (logical canvas + rectangular
  Stall rows). Versions are numbered per market. A version can be edited
  only until an event date uses it; after that it is locked for good, and
  changes go into a new version (a copy). So editing a plan can never
  silently change a date that already uses another or the same version.
- OccurrenceLayout: which LayoutVersion one EventOccurrence uses, plus that
  date's pricing revision and vendor-facing publication state.
- StallOffer: what a date sells: one per (occurrence, stall) with price
  (integer minor units), currency and enabled state. The service only
  accepts stalls of the date's selected version.

Nothing here reserves, holds or sells a stall. Phase 12 reservations will
reference StallOffer and copy its agreed price and currency.
"""

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower

from layouts.money import MAX_PRICE_MINOR, SUPPORTED_CURRENCIES
from markets.models import EventOccurrence, Market

MAX_CANVAS = 10_000


class LayoutVersion(models.Model):
    market = models.ForeignKey(Market, on_delete=models.PROTECT, related_name="layout_versions")
    # 1, 2, 3 ... per market.
    number = models.PositiveIntegerField()
    canvas_width = models.PositiveIntegerField()
    canvas_height = models.PositiveIntegerField()
    # Incremented by every saved edit while the version is a draft.
    revision = models.PositiveIntegerField(default=1)
    # Set when an event date first uses this version; never cleared. A
    # locked version (and its stalls) can't be edited.
    locked_at = models.DateTimeField(null=True, blank=True)
    based_on = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["market", "number"], name="layouts_version_number_unique"
            ),
            models.CheckConstraint(
                condition=Q(canvas_width__gte=1, canvas_width__lte=MAX_CANVAS)
                & Q(canvas_height__gte=1, canvas_height__lte=MAX_CANVAS),
                name="layouts_version_canvas_range",
            ),
            models.CheckConstraint(
                condition=Q(number__gte=1, revision__gte=1),
                name="layouts_version_number_revision_positive",
            ),
        ]

    def __str__(self):
        return f"Layout v{self.number} of market {self.market_id}"

    @property
    def is_locked(self) -> bool:
        return self.locked_at is not None


class PhysicalUnit(models.TextChoices):
    FEET = "FT", "feet"
    METERS = "M", "meters"


class Stall(models.Model):
    """A rectangle in one layout version. No price or availability."""

    layout_version = models.ForeignKey(
        LayoutVersion, on_delete=models.PROTECT, related_name="stalls"
    )
    label = models.CharField(max_length=40)
    description = models.TextField(max_length=500, blank=True)
    # Canvas rectangle, in logical canvas units from the top-left corner.
    x = models.PositiveIntegerField()
    y = models.PositiveIntegerField()
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    # Optional real-world size, e.g. 10 x 10 FT. Not related to canvas units.
    physical_width = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    physical_depth = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    physical_unit = models.CharField(max_length=2, choices=PhysicalUnit.choices, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                F("layout_version"), Lower("label"), name="layouts_stall_label_unique_in_version"
            ),
            models.CheckConstraint(condition=~Q(label=""), name="layouts_stall_label_not_blank"),
            models.CheckConstraint(
                condition=Q(width__gte=1, height__gte=1), name="layouts_stall_size_positive"
            ),
            # Physical size: all three set (positive), or none.
            models.CheckConstraint(
                condition=Q(
                    physical_width__isnull=True, physical_depth__isnull=True, physical_unit=""
                )
                | Q(
                    physical_width__gt=0,
                    physical_depth__gt=0,
                    physical_unit__in=PhysicalUnit.values,
                ),
                name="layouts_stall_physical_size_complete",
            ),
        ]

    def __str__(self):
        return f"Stall {self.label} in layout version {self.layout_version_id}"


class OccurrenceLayout(models.Model):
    """The layout version one event date uses, and that date's pricing state."""

    occurrence = models.OneToOneField(
        EventOccurrence, on_delete=models.PROTECT, related_name="stall_layout"
    )
    layout_version = models.ForeignKey(
        LayoutVersion, on_delete=models.PROTECT, related_name="occurrence_layouts"
    )
    # Incremented by every saved change to this date's offers or version.
    revision = models.PositiveIntegerField(default=1)
    # Set while vendors can see this date's layout and prices.
    published_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(revision__gte=1), name="layouts_occurrence_revision_positive"
            ),
        ]

    def __str__(self):
        return f"Occurrence {self.occurrence_id} uses layout version {self.layout_version_id}"

    @property
    def is_published(self) -> bool:
        return self.published_at is not None


class StallOffer(models.Model):
    """One stall offered on one date, with its price. Offers for stalls of a
    version the date no longer uses are kept but ignored."""

    occurrence = models.ForeignKey(
        EventOccurrence, on_delete=models.PROTECT, related_name="stall_offers"
    )
    stall = models.ForeignKey(Stall, on_delete=models.PROTECT, related_name="offers")
    # Integer minor units of ``currency`` (see layouts.money).
    price_minor = models.BigIntegerField()
    # ISO 4217, uppercase. One currency per date (enforced by the service).
    currency = models.CharField(max_length=3)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["occurrence", "stall"], name="layouts_offer_one_per_occurrence_stall"
            ),
            # Target of reservations' composite foreign key, which makes the
            # database reject a reservation whose offer is for another date.
            models.UniqueConstraint(
                fields=["id", "occurrence"], name="layouts_offer_id_occurrence_unique"
            ),
            models.CheckConstraint(
                condition=Q(price_minor__gte=0, price_minor__lte=MAX_PRICE_MINOR),
                name="layouts_offer_price_range",
            ),
            models.CheckConstraint(
                condition=Q(currency__in=SUPPORTED_CURRENCIES),
                name="layouts_offer_currency_supported",
            ),
        ]

    def __str__(self):
        return f"Offer of stall {self.stall_id} on occurrence {self.occurrence_id}"
