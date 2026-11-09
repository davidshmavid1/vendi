"""Bookings: a vendor business's stall on a date (Phase 13), and their
cancellations (Phase 14).

A Booking is created in the same transaction that confirms its reservation,
either after a verified payment (``payment_attempt`` set) or for a free
stall (``payment_required`` false, no attempt). It copies the reservation's
price snapshot, which stays authoritative whatever the offer costs later.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q

from applications.models import Application
from layouts.models import StallOffer
from layouts.money import MAX_PRICE_MINOR, SUPPORTED_CURRENCIES
from markets.models import EventOccurrence
from payments.models import PaymentAttempt
from reservations.models import Reservation
from vendors.models import VendorBusiness


class BookingStatus(models.TextChoices):
    CONFIRMED = "CONFIRMED", "Confirmed"
    CANCELLED = "CANCELLED", "Cancelled"


class Booking(models.Model):
    reservation = models.OneToOneField(
        Reservation, on_delete=models.PROTECT, related_name="booking"
    )
    # Copies of the reservation's references; a composite foreign key
    # (migration 0001) makes them match the reservation exactly.
    offer = models.ForeignKey(StallOffer, on_delete=models.PROTECT, related_name="bookings")
    occurrence = models.ForeignKey(
        EventOccurrence, on_delete=models.PROTECT, related_name="bookings"
    )
    application = models.ForeignKey(Application, on_delete=models.PROTECT, related_name="bookings")
    vendor_business = models.ForeignKey(
        VendorBusiness, on_delete=models.PROTECT, related_name="bookings"
    )
    price_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3)
    payment_required = models.BooleanField()
    payment_attempt = models.OneToOneField(
        PaymentAttempt,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="booking",
    )
    created_at = models.DateTimeField()
    # Booking state only. Whether the stall was released is the reservation's
    # state, and refund settlement is the Refund's: a CANCELLED booking may
    # still have a refund in flight.
    status = models.CharField(
        max_length=10, choices=BookingStatus.choices, default=BookingStatus.CONFIRMED
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    # Cancellation terms, copied from the reservation (see reservations).
    # ``policy_captured_at`` null: booked before policies existed; vendors
    # can't self-cancel it (manual review by the organizer).
    policy_captured_at = models.DateTimeField(null=True, blank=True)
    policy_vendor_cutoff_hours = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(status="CONFIRMED", cancelled_at__isnull=True)
                | Q(status="CANCELLED", cancelled_at__isnull=False),
                name="bookings_cancelled_at_matches",
            ),
            # Free bookings have no payment; paid ones exactly one.
            models.CheckConstraint(
                condition=Q(payment_required=True, payment_attempt__isnull=False, price_minor__gt=0)
                | Q(payment_required=False, payment_attempt__isnull=True, price_minor=0),
                name="bookings_payment_matches_price",
            ),
            models.CheckConstraint(
                condition=Q(price_minor__gte=0, price_minor__lte=MAX_PRICE_MINOR),
                name="bookings_price_range",
            ),
            models.CheckConstraint(
                condition=Q(currency__in=SUPPORTED_CURRENCIES),
                name="bookings_currency_supported",
            ),
        ]

    def __str__(self):
        return f"Booking {self.pk} of reservation {self.reservation_id}"


class CancellationKind(models.TextChoices):
    VENDOR = "VENDOR", "Cancelled by the vendor"
    ORGANIZER = "ORGANIZER", "Cancelled by the organizer"
    EVENT = "EVENT", "Date cancelled"


class RefundRule(models.TextChoices):
    # Everything paid back except Vendi's fee (the approved policy).
    PAID_MINUS_FEE = "PAID_MINUS_FEE", "Amount paid minus Vendi's fee"
    NO_PAYMENT = "NO_PAYMENT", "Free booking: nothing to refund"


class BookingCancellation(models.Model):
    """The one effective cancellation of a booking (unique), with the terms
    and the refund entitlement it was decided under. The booking, payment
    and reservation records are kept as they were."""

    booking = models.OneToOneField(Booking, on_delete=models.PROTECT, related_name="cancellation")
    kind = models.CharField(max_length=10, choices=CancellationKind.choices)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    # The authority it was requested with, e.g. "vendor:OWNER", "org:ADMIN".
    requested_as = models.CharField(max_length=20)
    # Visible to the vendor and the organizer.
    reason = models.TextField(max_length=1000, blank=True)
    # Organizer-only note; never shown to vendors.
    internal_note = models.TextField(max_length=1000, blank=True)
    requested_at = models.DateTimeField()
    completed_at = models.DateTimeField()
    policy_captured_at = models.DateTimeField(null=True, blank=True)
    policy_vendor_cutoff_hours = models.PositiveIntegerField(null=True, blank=True)
    refund_rule = models.CharField(max_length=20, choices=RefundRule.choices)
    refund_entitlement_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3)
    # The refund requested for it, if any (payments.Refund, reason CANCELLATION).
    refund = models.OneToOneField(
        "payments.Refund",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="booking_cancellation",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(kind__in=CancellationKind.values), name="bookings_cancellation_kind"
            ),
            models.CheckConstraint(
                condition=Q(refund_entitlement_minor__gte=0),
                name="bookings_cancellation_entitlement_positive",
            ),
            models.CheckConstraint(
                condition=Q(refund_rule="PAID_MINUS_FEE") | Q(refund_entitlement_minor=0),
                name="bookings_cancellation_free_refunds_nothing",
            ),
        ]

    def __str__(self):
        return f"Cancellation of booking {self.booking_id} ({self.kind})"


class OccurrenceCancellation(models.Model):
    """Work created when a date is cancelled (markets.cancel_occurrence, same
    transaction): one item per reservation that held or booked a stall.
    Items are processed in bounded batches, after the request, by
    ``process_occurrence_cancellation`` and ``reconcile_payments``."""

    occurrence = models.OneToOneField(
        EventOccurrence, on_delete=models.PROTECT, related_name="cancellation_run"
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    requested_as = models.CharField(max_length=20)
    requested_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Cancellation of occurrence {self.occurrence_id}"


class CancellationItemKind(models.TextChoices):
    HOLD = "HOLD", "Unpaid hold"
    CHECKOUT = "CHECKOUT", "Hold with a checkout in progress"
    BOOKING = "BOOKING", "Confirmed booking"


class CancellationItemStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    DONE = "DONE", "Done"


class OccurrenceCancellationItem(models.Model):
    run = models.ForeignKey(OccurrenceCancellation, on_delete=models.PROTECT, related_name="items")
    reservation = models.ForeignKey(Reservation, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=10, choices=CancellationItemKind.choices)
    status = models.CharField(
        max_length=10,
        choices=CancellationItemStatus.choices,
        default=CancellationItemStatus.PENDING,
    )
    attempts = models.PositiveIntegerField(default=0)
    # Short code of the last problem (never a secret or payload).
    last_error = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField()
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["run", "reservation"], name="bookings_cancellation_item_unique"
            ),
            models.CheckConstraint(
                condition=Q(status="PENDING", processed_at__isnull=True)
                | Q(status="DONE", processed_at__isnull=False),
                name="bookings_cancellation_item_processed_matches",
            ),
        ]
        indexes = [
            models.Index(
                fields=["created_at"],
                condition=Q(status="PENDING"),
                name="bookings_cancel_pending_idx",
            )
        ]

    def __str__(self):
        return f"{self.kind} {self.reservation_id} ({self.status})"
