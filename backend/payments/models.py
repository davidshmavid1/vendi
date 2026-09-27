"""Stall payments through Stripe Checkout (Phase 13).

Funds flow (the model already approved for the legacy app): destination
charges. The Checkout Session and its PaymentIntent live on Vendi's platform
Stripe account; the payment is transferred to the organizer's connected
account (``PaymentAccount.stripe_account_id``) minus the platform's
application fee (``application_fee_bps`` of the price).

Records, each with its own state so no single field mixes payment,
inventory and refund state:

- PaymentAttempt: one Checkout Session for one reservation. ``status`` is
  the payment state; ``fulfillment`` records whether a paid attempt got its
  stall (FULFILLED) or not (UNFULFILLED, which requires a refund).
- Refund: a compensating refund of an UNFULFILLED payment.
- StripeEvent: webhook deliveries, one row per Stripe event id.

No card details, client secrets or webhook payloads are stored.
"""

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from layouts.money import MAX_PRICE_MINOR, SUPPORTED_CURRENCIES
from organizations.models import Organization
from reservations.models import Reservation
from vendors.models import VendorBusiness

STRIPE_ACCOUNT_ID = r"^acct_[A-Za-z0-9]+$"


class PaymentAccount(models.Model):
    """An organization's Stripe connected account, linked by an operator
    (``manage.py link_stripe_account``) after Stripe confirms it can accept
    charges. There is no self-serve onboarding in the Django app yet."""

    organization = models.OneToOneField(
        Organization, on_delete=models.PROTECT, related_name="payment_account"
    )
    stripe_account_id = models.CharField(max_length=64)
    livemode = models.BooleanField()
    charges_enabled = models.BooleanField(default=False)
    # Platform's cut in basis points (100 = 1%), as in the legacy
    # Organization.applicationFeeBps.
    application_fee_bps = models.PositiveIntegerField(default=100)
    verified_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["livemode", "stripe_account_id"], name="payments_account_unique"
            ),
            models.CheckConstraint(
                condition=Q(stripe_account_id__regex=STRIPE_ACCOUNT_ID),
                name="payments_account_id_format",
            ),
            models.CheckConstraint(
                condition=Q(application_fee_bps__lte=10_000), name="payments_account_fee_range"
            ),
        ]

    def __str__(self):
        return f"{self.stripe_account_id} for organization {self.organization_id}"

    def fee_for(self, amount_minor: int) -> int:
        return amount_minor * self.application_fee_bps // 10_000


class AttemptStatus(models.TextChoices):
    # Saved before Stripe is called; the session may or may not exist yet.
    CREATING = "CREATING", "Creating"
    OPEN = "OPEN", "Open"  # session exists and can be paid
    SUCCEEDED = "SUCCEEDED", "Succeeded"  # Stripe says paid (verified)
    EXPIRED = "EXPIRED", "Expired"  # session expired unpaid
    CANCELED = "CANCELED", "Canceled"  # vendor cancelled; session expired at Stripe
    FAILED = "FAILED", "Failed"  # Stripe refused to create the session


# An attempt in one of these may still be paid, or has been: at most one per
# reservation, and its hold must not be released.
LIVE_ATTEMPT = (AttemptStatus.CREATING, AttemptStatus.OPEN, AttemptStatus.SUCCEEDED)
UNRESOLVED_ATTEMPT = (AttemptStatus.CREATING, AttemptStatus.OPEN)


class Fulfillment(models.TextChoices):
    FULFILLED = "FULFILLED", "Fulfilled"
    UNFULFILLED = "UNFULFILLED", "Unfulfilled"


class PaymentAttempt(models.Model):
    reservation = models.ForeignKey(
        Reservation, on_delete=models.PROTECT, related_name="payment_attempts"
    )
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="+")
    vendor_business = models.ForeignKey(
        VendorBusiness, on_delete=models.PROTECT, related_name="payment_attempts"
    )
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    # Provider context, fixed when the attempt is created.
    livemode = models.BooleanField()
    destination_account_id = models.CharField(max_length=64)
    # Snapshot from the reservation (never from the client).
    amount_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3)
    application_fee_minor = models.BigIntegerField()
    description = models.CharField(max_length=200)
    # Sent with every create call, so retries return the same session.
    idempotency_key = models.CharField(max_length=64, unique=True)
    session_expires_at = models.DateTimeField()
    checkout_session_id = models.CharField(max_length=255, blank=True, default="")
    checkout_url = models.URLField(max_length=2000, blank=True, default="")
    payment_intent_id = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(
        max_length=10, choices=AttemptStatus.choices, default=AttemptStatus.CREATING
    )
    fulfillment = models.CharField(
        max_length=11, choices=Fulfillment.choices, blank=True, default=""
    )
    # Recovery bookkeeping: calls made to Stripe, and the last problem as a
    # short code (never a secret or a payload).
    provider_calls = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=100, blank=True, default="")
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField()
    opened_at = models.DateTimeField(null=True, blank=True)
    succeeded_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    fulfilled_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # Target of Booking's composite foreign key (bookings.0001).
            models.UniqueConstraint(
                fields=["id", "reservation"], name="payments_attempt_id_reservation_unique"
            ),
            models.UniqueConstraint(
                fields=["reservation"],
                condition=Q(status__in=LIVE_ATTEMPT),
                name="payments_one_live_attempt_per_reservation",
            ),
            models.UniqueConstraint(
                fields=["livemode", "checkout_session_id"],
                condition=~Q(checkout_session_id=""),
                name="payments_attempt_session_unique",
            ),
            models.UniqueConstraint(
                fields=["livemode", "payment_intent_id"],
                condition=~Q(payment_intent_id=""),
                name="payments_attempt_intent_unique",
            ),
            models.CheckConstraint(
                condition=Q(status__in=AttemptStatus.values), name="payments_attempt_status_valid"
            ),
            models.CheckConstraint(
                condition=Q(amount_minor__gt=0, amount_minor__lte=MAX_PRICE_MINOR),
                name="payments_attempt_amount_range",
            ),
            models.CheckConstraint(
                condition=Q(application_fee_minor__gte=0)
                & Q(application_fee_minor__lte=F("amount_minor")),
                name="payments_attempt_fee_range",
            ),
            models.CheckConstraint(
                condition=Q(currency__in=SUPPORTED_CURRENCIES),
                name="payments_attempt_currency_supported",
            ),
            models.CheckConstraint(
                condition=Q(session_expires_at__gt=F("created_at")),
                name="payments_attempt_expires_after_created",
            ),
            # A session id exists once Stripe created one.
            models.CheckConstraint(
                condition=Q(status__in=["CREATING", "FAILED"]) | ~Q(checkout_session_id=""),
                name="payments_attempt_session_when_created",
            ),
            # Fulfillment is only meaningful for a successful payment.
            models.CheckConstraint(
                condition=(Q(status="SUCCEEDED", succeeded_at__isnull=False) & ~Q(fulfillment=""))
                | (~Q(status="SUCCEEDED") & Q(fulfillment="")),
                name="payments_attempt_fulfillment_when_paid",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "fulfillment"], name="payments_attempt_state_idx")
        ]

    def __str__(self):
        return f"PaymentAttempt {self.pk} ({self.status})"


class RefundStatus(models.TextChoices):
    # Saved before Stripe is called; the refund may or may not exist yet.
    REQUESTED = "REQUESTED", "Requested"
    PENDING = "PENDING", "Pending"  # Stripe accepted it; not final yet
    SUCCEEDED = "SUCCEEDED", "Succeeded"
    FAILED = "FAILED", "Failed"  # needs an operator
    CANCELED = "CANCELED", "Canceled"  # needs an operator


UNRESOLVED_REFUND = (RefundStatus.REQUESTED, RefundStatus.PENDING)


class RefundReason(models.TextChoices):
    # Paid, but the stall couldn't be confirmed (lost inventory or eligibility).
    UNFULFILLED = "UNFULFILLED", "Paid but not fulfilled"


class Refund(models.Model):
    attempt = models.ForeignKey(PaymentAttempt, on_delete=models.PROTECT, related_name="refunds")
    reason = models.CharField(max_length=12, choices=RefundReason.choices)
    amount_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3)
    idempotency_key = models.CharField(max_length=64, unique=True)
    stripe_refund_id = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(
        max_length=10, choices=RefundStatus.choices, default=RefundStatus.REQUESTED
    )
    provider_calls = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # One compensating refund per payment: the full amount, once.
            models.UniqueConstraint(
                fields=["attempt"],
                condition=Q(reason="UNFULFILLED"),
                name="payments_one_compensating_refund",
            ),
            models.UniqueConstraint(
                fields=["stripe_refund_id"],
                condition=~Q(stripe_refund_id=""),
                name="payments_refund_stripe_id_unique",
            ),
            models.CheckConstraint(
                condition=Q(status__in=RefundStatus.values), name="payments_refund_status_valid"
            ),
            models.CheckConstraint(
                condition=Q(amount_minor__gt=0), name="payments_refund_amount_positive"
            ),
        ]

    def __str__(self):
        return f"Refund {self.pk} of attempt {self.attempt_id} ({self.status})"


class StripeEvent(models.Model):
    """A verified webhook delivery. Only identifiers are kept; the payment
    state itself is always re-read from Stripe when processing."""

    event_id = models.CharField(max_length=255, unique=True)
    type = models.CharField(max_length=100)
    livemode = models.BooleanField()
    # Set for events from a connected account (Connect webhooks); unused by
    # destination charges, recorded so such events are never mistaken for
    # platform ones.
    account = models.CharField(max_length=64, blank=True, default="")
    object_id = models.CharField(max_length=255, blank=True, default="")
    stripe_created_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        indexes = [
            models.Index(
                fields=["received_at"],
                condition=Q(processed_at__isnull=True),
                name="payments_event_unprocessed_idx",
            )
        ]

    def __str__(self):
        return f"StripeEvent {self.event_id} ({self.type})"
