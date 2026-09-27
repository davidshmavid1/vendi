"""Application endpoints. Handlers only translate HTTP; rules live in
applications/services.py.

- Organizer: /api/v1/organizations/{organization_id}/...
- Vendor:    /api/v1/vendors/{business_id}/applications...
- Public:    /api/v1/public/occurrences/{id}/application,
             /api/v1/public/markets/{id}/application-windows
"""

from django.conf import settings
from django.utils import timezone
from ninja import Router, Status

from accounts.throttles import UserThrottle
from applications import services
from applications.models import Application
from applications.schemas import (
    ApplicationIn,
    ApplicationStatusName,
    DecisionIn,
    IntakeIn,
    IntakeOut,
    IntakeWindowList,
    OrganizerApplicationOut,
    OrganizerApplicationPage,
    PublicIntakeOut,
    VendorApplicationOut,
    VendorApplicationPage,
)
from core.auth import session_auth
from core.pagination import paginate
from core.schemas import ErrorOut
from markets import public
from markets.api import occurrence_dict

organizer_router = Router(tags=["applications"], auth=session_auth)
vendor_router = Router(tags=["applications"], auth=session_auth)
public_router = Router(tags=["public"])

_ERRORS = {400: ErrorOut, 401: ErrorOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut}
_RATES = settings.APPLICATION_RATE_LIMITS


# --- Serialization --------------------------------------------------------------------


def _occurrence(application: Application) -> dict:
    occurrence = application.occurrence
    return {
        "id": occurrence.pk,
        "market_id": occurrence.market_id,
        "market_name": occurrence.market.name,
        "starts_at": occurrence.starts_at,
        "ends_at": occurrence.ends_at,
        "timezone": occurrence.market.timezone,
        "status": occurrence.status,
    }


def _fields(application: Application) -> dict:
    return {
        "id": application.pk,
        "status": application.status,
        "vendor_business_id": application.vendor_business_id,
        "occurrence": _occurrence(application),
        "questions_version": application.questions_version,
        "questions": application.questions,
        "answers": application.answers,
        "vendor_snapshot": application.vendor_snapshot,
        "submitted_at": application.submitted_at,
        "decided_at": application.decided_at,
        "decision_message": application.decision_message,
        "withdrawn_at": application.withdrawn_at,
    }


def _vendor_view(application: Application) -> dict:
    return _fields(application) | {
        "organizer_name": application.occurrence.market.organization.name
    }


def _organizer_view(application: Application) -> dict:
    return _fields(application) | {
        "submitted_by_user_id": application.submitted_by_id,
        "decided_by_user_id": application.decided_by_id,
        "withdrawn_by_user_id": application.withdrawn_by_id,
        "history": [
            {
                "from_status": event.from_status,
                "to_status": event.to_status,
                "actor_user_id": event.actor_id,
                "at": event.created_at,
            }
            for event in application.events.order_by("pk")
        ],
    }


def _summary(application: Application) -> dict:
    return {
        "id": application.pk,
        "status": application.status,
        "vendor_business_id": application.vendor_business_id,
        "vendor_name": application.vendor_snapshot["name"],
        "occurrence": _occurrence(application),
        "submitted_at": application.submitted_at,
        "decided_at": application.decided_at,
    }


def _intake(occurrence, intake) -> dict:
    return {
        "occurrence_id": occurrence.pk,
        "configured": intake is not None,
        "enabled": bool(intake and intake.enabled),
        "opens_at": intake.opens_at if intake else None,
        "closes_at": intake.closes_at if intake else None,
        "instructions": intake.instructions if intake else "",
        "questions_version": intake.questions_version if intake else 1,
        "questions": intake.questions if intake else [],
        "state": services.intake_state(occurrence, intake),
        "updated_at": intake.updated_at if intake else None,
    }


# --- Public ---------------------------------------------------------------------------


@public_router.get(
    "/occurrences/{occurrence_id}/application", response={200: PublicIntakeOut, 404: ErrorOut}
)
def public_intake(request, occurrence_id: int):
    """Instructions and questions for applying to a published market's date."""
    occurrence = public.get_occurrence(occurrence_id)
    intake = services.intake_for(occurrence)
    state = services.intake_state(occurrence, intake)
    visible = intake is not None and state in (services.OPEN, services.NOT_OPEN_YET)
    return {
        "occurrence": occurrence_dict(occurrence, public_view=True),
        "market_name": occurrence.market.name,
        "organizer_name": occurrence.market.organization.name,
        "state": state,
        "opens_at": intake.opens_at if visible else None,
        "closes_at": intake.closes_at if visible else None,
        "instructions": intake.instructions if visible else "",
        "questions_version": intake.questions_version if visible else None,
        "questions": intake.questions if visible else [],
    }


@public_router.get(
    "/markets/{market_id}/application-windows", response={200: IntakeWindowList, 404: ErrorOut}
)
def public_windows(request, market_id: int):
    """Application state of each date that hasn't started yet (cancelled ones
    report ``closed``)."""
    occurrences = (
        public.upcoming_occurrences(market_id)
        .filter(starts_at__gt=timezone.now())
        .select_related("market", "application_intake")
        .order_by("starts_at")[:100]
    )
    items = []
    for occurrence in occurrences:
        intake = getattr(occurrence, "application_intake", None)
        state = services.intake_state(occurrence, intake)
        visible = state in (services.OPEN, services.NOT_OPEN_YET)
        items.append(
            {
                "occurrence_id": occurrence.pk,
                "state": state,
                "opens_at": intake.opens_at if visible else None,
                "closes_at": intake.closes_at if visible else None,
            }
        )
    return {"items": items}


# --- Organizer ------------------------------------------------------------------------

_SETTINGS = (
    "/{organization_id}/markets/{market_id}/occurrences/{occurrence_id}/application-settings"
)


@organizer_router.get(_SETTINGS, response={200: IntakeOut, **_ERRORS})
def get_settings(request, organization_id: int, market_id: int, occurrence_id: int):
    return _intake(*services.get_intake(request.auth, organization_id, market_id, occurrence_id))


@organizer_router.put(_SETTINGS, response={200: IntakeOut, 422: ErrorOut, **_ERRORS})
def put_settings(
    request, organization_id: int, market_id: int, occurrence_id: int, payload: IntakeIn
):
    values = payload.model_dump()
    return _intake(
        *services.configure_intake(
            request.auth, organization_id, market_id, occurrence_id, **values
        )
    )


@organizer_router.get(
    "/{organization_id}/applications", response={200: OrganizerApplicationPage, **_ERRORS}
)
def list_organization_applications(
    request,
    organization_id: int,
    status: ApplicationStatusName | None = None,
    market_id: int | None = None,
    occurrence_id: int | None = None,
    cursor: int | None = None,
    limit: int | None = None,
):
    queryset = services.list_for_organization(
        request.auth,
        organization_id,
        status=status,
        market_id=market_id,
        occurrence_id=occurrence_id,
    )
    items, next_cursor = paginate(queryset, cursor=cursor, limit=limit)
    return {"items": [_summary(a) for a in items], "next_cursor": next_cursor}


@organizer_router.get(
    "/{organization_id}/applications/{application_id}",
    response={200: OrganizerApplicationOut, **_ERRORS},
)
def get_organization_application(request, organization_id: int, application_id: int):
    return _organizer_view(
        services.get_for_organization(request.auth, organization_id, application_id)
    )


def _decide(request, organization_id: int, application_id: int, payload: DecisionIn, approve):
    return _organizer_view(
        services.decide(
            request.auth, organization_id, application_id, approve=approve, message=payload.message
        )
    )


@organizer_router.post(
    "/{organization_id}/applications/{application_id}/approve",
    response={200: OrganizerApplicationOut, 422: ErrorOut, **_ERRORS},
)
def approve(request, organization_id: int, application_id: int, payload: DecisionIn):
    """Approve a submitted application. Does not reserve a stall or take payment."""
    return _decide(request, organization_id, application_id, payload, approve=True)


@organizer_router.post(
    "/{organization_id}/applications/{application_id}/reject",
    response={200: OrganizerApplicationOut, 422: ErrorOut, **_ERRORS},
)
def reject(request, organization_id: int, application_id: int, payload: DecisionIn):
    return _decide(request, organization_id, application_id, payload, approve=False)


# --- Vendor ---------------------------------------------------------------------------


@vendor_router.post(
    "/{business_id}/applications",
    response={201: VendorApplicationOut, 422: ErrorOut, 429: ErrorOut, **_ERRORS},
    throttle=[UserThrottle("submit_user", _RATES)],
)
def submit(request, business_id: int, payload: ApplicationIn):
    application = services.submit(
        request.auth,
        business_id,
        occurrence_id=payload.occurrence_id,
        questions_version=payload.questions_version,
        answers=payload.answers,
    )
    return Status(201, _vendor_view(application))


@vendor_router.get("/{business_id}/applications", response={200: VendorApplicationPage, **_ERRORS})
def list_business_applications(
    request,
    business_id: int,
    status: ApplicationStatusName | None = None,
    occurrence_id: int | None = None,
    cursor: int | None = None,
    limit: int | None = None,
):
    queryset = services.list_for_business(
        request.auth, business_id, status=status, occurrence_id=occurrence_id
    )
    items, next_cursor = paginate(queryset, cursor=cursor, limit=limit)
    return {"items": [_vendor_view(a) for a in items], "next_cursor": next_cursor}


@vendor_router.get(
    "/{business_id}/applications/{application_id}", response={200: VendorApplicationOut, **_ERRORS}
)
def get_business_application(request, business_id: int, application_id: int):
    return _vendor_view(services.get_for_business(request.auth, business_id, application_id))


@vendor_router.post(
    "/{business_id}/applications/{application_id}/withdraw",
    response={200: VendorApplicationOut, **_ERRORS},
)
def withdraw(request, business_id: int, application_id: int):
    return _vendor_view(services.withdraw(request.auth, business_id, application_id))
