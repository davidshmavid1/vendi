"""Vendor applications to market event dates.

ApplicationIntake is the organizer's configuration for one EventOccurrence:
whether applications are accepted, when, and which questions are asked.
Application is one vendor business's application to one occurrence. It keeps
an immutable copy of the questions it answered and of the business details
reviewers saw, so later edits change neither. ApplicationEvent records every
status change with its actor. Applications are never deleted.

Approval does not reserve a stall or take payment (later phases).
"""

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from markets.models import EventOccurrence
from vendors.models import VendorBusiness


class ApplicationIntake(models.Model):
    occurrence = models.OneToOneField(
        EventOccurrence, on_delete=models.PROTECT, related_name="application_intake"
    )
    enabled = models.BooleanField(default=False)
    # Optional window. Submissions also always stop when the event starts.
    opens_at = models.DateTimeField(null=True, blank=True)
    closes_at = models.DateTimeField(null=True, blank=True)
    instructions = models.TextField(max_length=5000, blank=True)
    # Validated by applications.questions; see that module for the shape.
    questions = models.JSONField(default=list)
    # Bumped whenever ``questions`` changes. Each application stores the
    # version and a copy of the questions it answered.
    questions_version = models.PositiveIntegerField(default=1)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(opens_at__isnull=True)
                | Q(closes_at__isnull=True)
                | Q(closes_at__gt=F("opens_at")),
                name="applications_intake_window_order",
            ),
            models.CheckConstraint(
                condition=Q(questions_version__gte=1), name="applications_intake_version_positive"
            ),
        ]

    def __str__(self):
        return f"Application intake for occurrence {self.occurrence_id}"


class ApplicationStatus(models.TextChoices):
    SUBMITTED = "SUBMITTED", "Submitted"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    WITHDRAWN = "WITHDRAWN", "Withdrawn"


DECIDED = (ApplicationStatus.APPROVED, ApplicationStatus.REJECTED)


class Application(models.Model):
    occurrence = models.ForeignKey(
        EventOccurrence, on_delete=models.PROTECT, related_name="applications"
    )
    # The authoritative vendor identity. ``vendor_snapshot`` is only a record
    # of what was shown to reviewers at submission; it is never edited.
    vendor_business = models.ForeignKey(
        VendorBusiness, on_delete=models.PROTECT, related_name="applications"
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    status = models.CharField(
        max_length=10, choices=ApplicationStatus.choices, default=ApplicationStatus.SUBMITTED
    )
    questions_version = models.PositiveIntegerField()
    questions = models.JSONField()
    answers = models.JSONField()
    vendor_snapshot = models.JSONField()
    submitted_at = models.DateTimeField()
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    # Shown to the vendor. There are no reviewer-only notes in this phase.
    decision_message = models.TextField(max_length=2000, blank=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    withdrawn_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["occurrence", "vendor_business"],
                name="applications_one_per_business_occurrence",
            ),
            models.CheckConstraint(
                condition=Q(status__in=ApplicationStatus.values),
                name="applications_status_valid",
            ),
            # Decision fields are set exactly when the application was decided.
            models.CheckConstraint(
                condition=Q(status__in=DECIDED, decided_at__isnull=False, decided_by__isnull=False)
                | (
                    ~Q(status__in=DECIDED)
                    & Q(decided_at__isnull=True, decided_by__isnull=True)
                    & Q(decision_message="")
                ),
                name="applications_decision_fields_match",
            ),
            models.CheckConstraint(
                condition=Q(
                    status="WITHDRAWN", withdrawn_at__isnull=False, withdrawn_by__isnull=False
                )
                | (
                    ~Q(status="WITHDRAWN") & Q(withdrawn_at__isnull=True, withdrawn_by__isnull=True)
                ),
                name="applications_withdrawal_fields_match",
            ),
        ]
        indexes = [
            models.Index(fields=["occurrence", "status"], name="applications_occ_status_idx"),
        ]

    def __str__(self):
        return f"Application {self.pk} ({self.status})"


class ApplicationEvent(models.Model):
    """Append-only history of status changes (including the submission)."""

    application = models.ForeignKey(Application, on_delete=models.PROTECT, related_name="events")
    from_status = models.CharField(max_length=10, blank=True)
    to_status = models.CharField(max_length=10, choices=ApplicationStatus.choices)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(to_status__in=ApplicationStatus.values),
                name="applications_event_status_valid",
            ),
        ]

    def __str__(self):
        return f"{self.application_id}: {self.from_status or '-'} -> {self.to_status}"
