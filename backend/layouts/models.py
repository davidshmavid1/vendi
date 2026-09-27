"""Stall layouts: a flat, two-dimensional plan of rectangular stalls for one
event date, with a price per stall.

Each EventOccurrence has at most one StallLayout, and its Stall rows belong
only to it, so editing one date never changes another. Positions and sizes
are in the layout's logical canvas units (integers, independent of screen
size); a stall's real-world size is stored separately with an explicit unit.

A stall here is a sellable space as configured by the organizer. It has no
availability, hold, booking or payment state: that belongs to later phases.
Stalls are never deleted; organizers disable them instead.
"""

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower

from layouts.money import MAX_PRICE_MINOR, SUPPORTED_CURRENCIES
from markets.models import EventOccurrence

MAX_CANVAS = 10_000


class StallLayout(models.Model):
    occurrence = models.OneToOneField(
        EventOccurrence, on_delete=models.PROTECT, related_name="stall_layout"
    )
    canvas_width = models.PositiveIntegerField()
    canvas_height = models.PositiveIntegerField()
    # ISO 4217, uppercase; one currency for every stall in the layout.
    currency = models.CharField(max_length=3)
    # Incremented by every saved edit; clients send the revision they edited
    # so a stale save is rejected instead of overwriting someone's work.
    revision = models.PositiveIntegerField(default=1)
    # Set while vendors can see the layout; null = organizer-only.
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
                condition=Q(canvas_width__gte=1, canvas_width__lte=MAX_CANVAS)
                & Q(canvas_height__gte=1, canvas_height__lte=MAX_CANVAS),
                name="layouts_layout_canvas_range",
            ),
            models.CheckConstraint(
                condition=Q(currency__in=SUPPORTED_CURRENCIES),
                name="layouts_layout_currency_supported",
            ),
            models.CheckConstraint(
                condition=Q(revision__gte=1), name="layouts_layout_revision_positive"
            ),
        ]

    def __str__(self):
        return f"Layout for occurrence {self.occurrence_id} (rev {self.revision})"

    @property
    def is_published(self) -> bool:
        return self.published_at is not None


class PhysicalUnit(models.TextChoices):
    FEET = "FT", "feet"
    METERS = "M", "meters"


class Stall(models.Model):
    layout = models.ForeignKey(StallLayout, on_delete=models.PROTECT, related_name="stalls")
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
    enabled = models.BooleanField(default=True)
    # Integer minor units of the layout's currency (see layouts.money).
    price_minor = models.BigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                F("layout"), Lower("label"), name="layouts_stall_label_unique_in_layout"
            ),
            models.CheckConstraint(condition=~Q(label=""), name="layouts_stall_label_not_blank"),
            models.CheckConstraint(
                condition=Q(width__gte=1, height__gte=1), name="layouts_stall_size_positive"
            ),
            models.CheckConstraint(
                condition=Q(price_minor__gte=0, price_minor__lte=MAX_PRICE_MINOR),
                name="layouts_stall_price_range",
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
        return f"Stall {self.label} in layout {self.layout_id}"
