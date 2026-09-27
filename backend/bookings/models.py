"""Confirmed bookings: a vendor business's stall on a date (Phase 13).

A Booking is created in the same transaction that confirms its reservation,
either after a verified payment (``payment_attempt`` set) or for a free
stall (``payment_required`` false, no attempt). It copies the reservation's
price snapshot, which stays authoritative whatever the offer costs later.
"""

from django.db import models
from django.db.models import Q

from applications.models import Application
from layouts.models import StallOffer
from layouts.money import MAX_PRICE_MINOR, SUPPORTED_CURRENCIES
from markets.models import EventOccurrence
from payments.models import PaymentAttempt
from reservations.models import Reservation
from vendors.models import VendorBusiness


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

    class Meta:
        constraints = [
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
