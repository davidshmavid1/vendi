"""Application intake, submission, withdrawal and review.

Permissions (decided from the caller's current memberships on every call):

    action                                  who
    configure intake for an occurrence      organization OWNER, ADMIN
    list / view organization applications   organization OWNER, ADMIN, STAFF
    approve / reject                        organization OWNER, ADMIN
    submit / withdraw                       vendor business OWNER
    list / view a business's applications   vendor business OWNER, MEMBER

Vendor membership grants nothing on the organizer side, and organization
membership grants nothing on the vendor side.

Locking (always in this order: application, occurrence, intake):
- Configuring intake and submitting lock the occurrence row, then its
  ApplicationIntake row, so a submission always sees the questions and
  window it is checked against, and a concurrent cancellation or move of the
  date waits for it. Occurrence locks are FOR NO KEY UPDATE, so inserting
  rows that reference the occurrence (the application itself) isn't blocked.
- Decisions and withdrawals lock the Application row, so only one transition
  out of SUBMITTED can win; approval then locks the occurrence to check it.
- The unique constraint on (occurrence, vendor_business) is the final guard
  against duplicates.
"""

from datetime import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import User
from applications.models import (
    Application,
    ApplicationEvent,
    ApplicationIntake,
    ApplicationStatus,
)
from applications.questions import clean_answers, normalize_questions
from core.exceptions import Conflict, InvalidRequest, NotFound, PermissionDenied
from markets.models import EventOccurrence, MarketStatus, OccurrenceStatus
from moderation.policy import ensure_can_participate, is_restricted
from organizations.models import Role
from organizations.permissions import membership_for as organization_membership
from organizations.permissions import require_role
from vendors.permissions import membership_for as vendor_membership
from vendors.permissions import require_owner

# Intake states shown to vendors.
OPEN = "open"
NOT_OPEN_YET = "not_open_yet"
CLOSED = "closed"
NOT_ACCEPTING = "not_accepting"

SNAPSHOT_FIELDS = (
    "name",
    "category",
    "description",
    "contact_email",
    "phone",
    "website",
    "city",
    "region",
)


def _require_verified(user: User) -> None:
    if not user.is_email_verified:
        raise PermissionDenied(
            "Confirm your email address before doing this.", code="email_not_verified"
        )


def _aware(value: datetime | None, field: str) -> datetime | None:
    if value is not None and timezone.is_naive(value):
        raise InvalidRequest(f"{field} must include a UTC offset.", code="timestamp_invalid")
    return value


def intake_state(occurrence: EventOccurrence, intake: ApplicationIntake | None, now=None) -> str:
    """Whether a vendor could submit right now (ignoring who they are)."""
    now = now or timezone.now()
    if (
        occurrence.market.status != MarketStatus.PUBLISHED
        or occurrence.status != OccurrenceStatus.SCHEDULED
        or now >= occurrence.starts_at
    ):
        return CLOSED
    if intake is None or not intake.enabled:
        return NOT_ACCEPTING
    if intake.opens_at and now < intake.opens_at:
        return NOT_OPEN_YET
    if intake.closes_at and now >= intake.closes_at:
        return CLOSED
    return OPEN


def intake_for(occurrence: EventOccurrence) -> ApplicationIntake | None:
    return ApplicationIntake.objects.filter(occurrence=occurrence).first()


# --- Intake configuration (organizer) -------------------------------------------------


def _occurrence_in(organization_id: int, market_id: int, occurrence_id: int, *, lock=False):
    queryset = EventOccurrence.objects.select_related("market").filter(
        pk=occurrence_id, market_id=market_id, market__organization_id=organization_id
    )
    occurrence = (_lock_occurrences(queryset) if lock else queryset).first()
    if occurrence is None:
        raise NotFound("Event date not found.")
    return occurrence


def _lock_occurrences(queryset):
    return queryset.select_for_update(of=("self",), no_key=True)


def get_intake(actor: User, organization_id: int, market_id: int, occurrence_id: int):
    """(occurrence, intake or None). Any organization member may read."""
    organization_membership(actor, organization_id)
    occurrence = _occurrence_in(organization_id, market_id, occurrence_id)
    return occurrence, intake_for(occurrence)


def configure_intake(
    actor: User,
    organization_id: int,
    market_id: int,
    occurrence_id: int,
    *,
    enabled: bool,
    opens_at: datetime | None,
    closes_at: datetime | None,
    instructions: str,
    questions: list[dict],
):
    """Replace the occurrence's intake settings. Bumps ``questions_version``
    only when the questions actually change."""
    _aware(opens_at, "opens_at")
    _aware(closes_at, "closes_at")
    cleaned_questions = normalize_questions(questions)
    with transaction.atomic():
        require_role(organization_membership(actor, organization_id), Role.OWNER, Role.ADMIN)
        # Lock the occurrence first (same order as occurrence edits), then
        # the intake row, so a concurrent submission sees old or new settings.
        occurrence = _occurrence_in(organization_id, market_id, occurrence_id, lock=True)
        if occurrence.market.status == MarketStatus.ARCHIVED:
            raise Conflict("Archived markets can't be changed.", code="market_archived")
        if opens_at and closes_at and closes_at <= opens_at:
            raise InvalidRequest("closes_at must be after opens_at.", code="window_invalid")
        if closes_at and closes_at > occurrence.starts_at:
            raise InvalidRequest(
                "Applications must close by the time the event starts.", code="window_invalid"
            )
        if opens_at and opens_at >= occurrence.starts_at:
            raise InvalidRequest(
                "Applications must open before the event starts.", code="window_invalid"
            )
        intake = ApplicationIntake.objects.select_for_update().filter(occurrence=occurrence).first()
        if intake is None:
            intake = ApplicationIntake(occurrence=occurrence, questions=cleaned_questions)
        elif intake.questions != cleaned_questions:
            intake.questions = cleaned_questions
            intake.questions_version += 1
        intake.enabled = enabled
        intake.opens_at = opens_at
        intake.closes_at = closes_at
        intake.instructions = instructions.strip()
        intake.updated_by = actor
        intake.save()
    return occurrence, intake


# --- Vendor side ----------------------------------------------------------------------


def _record(application: Application, from_status: str, actor: User) -> None:
    ApplicationEvent.objects.create(
        application=application,
        from_status=from_status,
        to_status=application.status,
        actor=actor,
    )


def submit(
    actor: User,
    business_id: int,
    *,
    occurrence_id: int,
    questions_version: int,
    answers: dict,
) -> Application:
    _require_verified(actor)
    membership = vendor_membership(actor, business_id)
    require_owner(membership)
    business = membership.business
    try:
        with transaction.atomic():
            occurrence = _lock_occurrences(
                EventOccurrence.objects.select_related("market").filter(
                    pk=occurrence_id, market__status=MarketStatus.PUBLISHED
                )
            ).first()
            if occurrence is None:
                raise NotFound("Event date not found.")
            intake = (
                ApplicationIntake.objects.select_for_update().filter(occurrence=occurrence).first()
            )
            state = intake_state(occurrence, intake)
            if state != OPEN:
                raise Conflict(
                    _closed_message(state), code="applications_closed", details=[{"state": state}]
                )
            ensure_can_participate(
                occurrence.market.organization_id, account=actor, vendor_business=business
            )
            if questions_version != intake.questions_version:
                raise Conflict(
                    "The questions changed while you were applying. Reload and try again.",
                    code="questions_changed",
                )
            cleaned = clean_answers(intake.questions, answers)
            existing = Application.objects.filter(
                occurrence=occurrence, vendor_business=business
            ).first()
            if existing is not None:
                raise _duplicate(existing)
            application = Application.objects.create(
                occurrence=occurrence,
                vendor_business=business,
                submitted_by=actor,
                questions_version=intake.questions_version,
                questions=intake.questions,
                answers=cleaned,
                vendor_snapshot={f: getattr(business, f) for f in SNAPSHOT_FIELDS},
                submitted_at=timezone.now(),
            )
            _record(application, "", actor)
    except IntegrityError as error:
        # Lost a race to another submission for the same business.
        existing = Application.objects.filter(
            occurrence_id=occurrence_id, vendor_business=business
        ).first()
        if existing is None:
            raise
        raise _duplicate(existing) from error
    return application


def _closed_message(state: str) -> str:
    return {
        NOT_OPEN_YET: "Applications for this date aren't open yet.",
        NOT_ACCEPTING: "This date isn't accepting applications.",
    }.get(state, "Applications for this date are closed.")


def _duplicate(existing: Application) -> Conflict:
    return Conflict(
        "This business has already applied to this date.",
        code="application_exists",
        details=[{"application_id": existing.pk, "status": existing.status}],
    )


def list_for_business(
    actor: User, business_id: int, *, status: str | None = None, occurrence_id: int | None = None
):
    vendor_membership(actor, business_id)
    queryset = Application.objects.filter(vendor_business_id=business_id).select_related(
        "occurrence__market__organization"
    )
    if status:
        queryset = queryset.filter(status=status)
    if occurrence_id:
        queryset = queryset.filter(occurrence_id=occurrence_id)
    return queryset


def get_for_business(actor: User, business_id: int, application_id: int) -> Application:
    vendor_membership(actor, business_id)
    return _application(vendor_business_id=business_id, pk=application_id)


def withdraw(actor: User, business_id: int, application_id: int) -> Application:
    require_owner(vendor_membership(actor, business_id))
    with transaction.atomic():
        application = _application(vendor_business_id=business_id, pk=application_id, lock=True)
        _require_submitted(application)
        application.status = ApplicationStatus.WITHDRAWN
        application.withdrawn_at = timezone.now()
        application.withdrawn_by = actor
        application.save(update_fields=["status", "withdrawn_at", "withdrawn_by", "updated_at"])
        _record(application, ApplicationStatus.SUBMITTED, actor)
    return application


# --- Organizer side -------------------------------------------------------------------


def _application(*, lock=False, **filters) -> Application:
    queryset = Application.objects.filter(**filters)
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    application = queryset.select_related("occurrence__market__organization").first()
    if application is None:
        raise NotFound("Application not found.")
    return application


def _require_submitted(application: Application) -> None:
    if application.status != ApplicationStatus.SUBMITTED:
        raise Conflict(
            f"This application is already {application.status.lower()}.",
            code="application_not_submitted",
            details=[{"status": application.status}],
        )


def list_for_organization(
    actor: User,
    organization_id: int,
    *,
    status: str | None = None,
    market_id: int | None = None,
    occurrence_id: int | None = None,
):
    organization_membership(actor, organization_id)
    queryset = Application.objects.filter(
        occurrence__market__organization_id=organization_id
    ).select_related("occurrence__market")
    if status:
        queryset = queryset.filter(status=status)
    if market_id:
        queryset = queryset.filter(occurrence__market_id=market_id)
    if occurrence_id:
        queryset = queryset.filter(occurrence_id=occurrence_id)
    return queryset


def get_for_organization(actor: User, organization_id: int, application_id: int) -> Application:
    organization_membership(actor, organization_id)
    return _application(occurrence__market__organization_id=organization_id, pk=application_id)


def decide(
    actor: User, organization_id: int, application_id: int, *, approve: bool, message: str = ""
) -> Application:
    require_role(organization_membership(actor, organization_id), Role.OWNER, Role.ADMIN)
    with transaction.atomic():
        application = _application(
            occurrence__market__organization_id=organization_id, pk=application_id, lock=True
        )
        _require_submitted(application)
        if approve:
            _check_approvable(application, organization_id)
        application.status = ApplicationStatus.APPROVED if approve else ApplicationStatus.REJECTED
        application.decided_at = timezone.now()
        application.decided_by = actor
        application.decision_message = message.strip()
        application.save(
            update_fields=["status", "decided_at", "decided_by", "decision_message", "updated_at"]
        )
        _record(application, ApplicationStatus.SUBMITTED, actor)
    return application


def _check_approvable(application: Application, organization_id: int) -> None:
    occurrence = _lock_occurrences(
        EventOccurrence.objects.select_related("market").filter(pk=application.occurrence_id)
    ).get()
    if (
        occurrence.market.status != MarketStatus.PUBLISHED
        or occurrence.status != OccurrenceStatus.SCHEDULED
        or occurrence.starts_at <= timezone.now()
    ):
        raise Conflict(
            "This date is cancelled, over or no longer public, so it can't be approved. "
            "You can still reject the application.",
            code="occurrence_unavailable",
        )
    # Re-check moderation: a restriction may have been added after submission.
    if is_restricted(
        organization_id,
        account=application.submitted_by,
        vendor_business=application.vendor_business,
    ):
        raise Conflict(
            "The vendor business or the account that applied is restricted in this "
            "organization. Lift the restriction first, or reject the application.",
            code="applicant_restricted",
        )
