"""Layout endpoints. Handlers only translate HTTP; rules live in
layouts/services.py.

- Versions: /api/v1/organizations/{org}/markets/{market}/layout-versions[/{id}]
- A date:   /api/v1/organizations/{org}/markets/{market}/occurrences/{occ}/layout
- Public:   /api/v1/public/occurrences/{occ}/layout
"""

from ninja import Router, Status

from core.auth import session_auth
from core.schemas import ErrorOut
from layouts import services
from layouts.money import exponent
from layouts.schemas import (
    DateLayoutIn,
    DateLayoutOut,
    PublicLayoutOut,
    PublishIn,
    VersionCreateIn,
    VersionIn,
    VersionList,
    VersionOut,
)

router = Router(tags=["layouts"], auth=session_auth)
public_router = Router(tags=["public"])

_ERRORS = {400: ErrorOut, 401: ErrorOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut}
_VERSIONS = "/{organization_id}/markets/{market_id}/layout-versions"
_DATE = "/{organization_id}/markets/{market_id}/occurrences/{occurrence_id}/layout"
_GEOMETRY = ("x", "y", "width", "height", "physical_width", "physical_depth")


def _stall(stall) -> dict:
    return {
        "id": stall.pk,
        "label": stall.label,
        "description": stall.description,
        **{f: getattr(stall, f) for f in _GEOMETRY},
        "physical_unit": stall.physical_unit or None,
    }


def _version(version, stalls) -> dict:
    return {
        "id": version.pk,
        "market_id": version.market_id,
        "number": version.number,
        "canvas_width": version.canvas_width,
        "canvas_height": version.canvas_height,
        "revision": version.revision,
        "locked": version.is_locked,
        "locked_at": version.locked_at,
        "based_on_id": version.based_on_id,
        "created_at": version.created_at,
        "updated_at": version.updated_at,
        "stalls": [_stall(s) for s in stalls],
    }


def _date(result) -> dict:
    occurrence, occurrence_layout, stalls, offers = result
    currency = offers[0].currency if offers else None
    return {
        "occurrence_id": occurrence.pk,
        "market_id": occurrence.market_id,
        "layout_version": _version(occurrence_layout.layout_version, stalls),
        "currency": currency,
        "currency_exponent": exponent(currency) if currency else None,
        "revision": occurrence_layout.revision,
        "published": occurrence_layout.is_published,
        "published_at": occurrence_layout.published_at,
        "offers": [
            {
                "id": o.pk,
                "stall_id": o.stall_id,
                "price_minor": o.price_minor,
                "currency": o.currency,
                "enabled": o.enabled,
            }
            for o in offers
        ],
    }


# --- Layout versions ------------------------------------------------------------------


@router.get(_VERSIONS, response={200: VersionList, **_ERRORS})
def list_versions(request, organization_id: int, market_id: int):
    versions = services.list_versions(request.auth, organization_id, market_id)
    return {
        "items": [
            {
                "id": v.pk,
                "number": v.number,
                "canvas_width": v.canvas_width,
                "canvas_height": v.canvas_height,
                "revision": v.revision,
                "locked": v.is_locked,
                "based_on_id": v.based_on_id,
                "stall_count": v.stall_count,
                "date_count": v.date_count,
                "updated_at": v.updated_at,
            }
            for v in versions
        ]
    }


@router.post(_VERSIONS, response={201: VersionOut, 422: ErrorOut, **_ERRORS})
def create_version(request, organization_id: int, market_id: int, payload: VersionCreateIn):
    """A new editable layout, empty/from the payload, or a copy of ``copy_of``."""
    values = payload.model_dump()
    return Status(
        201, _version(*services.create_version(request.auth, organization_id, market_id, **values))
    )


@router.get(_VERSIONS + "/{version_id}", response={200: VersionOut, **_ERRORS})
def get_version(request, organization_id: int, market_id: int, version_id: int):
    return _version(*services.get_version(request.auth, organization_id, market_id, version_id))


@router.put(_VERSIONS + "/{version_id}", response={200: VersionOut, 422: ErrorOut, **_ERRORS})
def save_version(
    request, organization_id: int, market_id: int, version_id: int, payload: VersionIn
):
    """Replace a draft version's plan atomically. Send every existing stall."""
    values = payload.model_dump()
    return _version(
        *services.save_version(request.auth, organization_id, market_id, version_id, **values)
    )


# --- A date's layout and offers ---------------------------------------------------------


@router.get(_DATE, response={200: DateLayoutOut, **_ERRORS})
def get_date_layout(request, organization_id: int, market_id: int, occurrence_id: int):
    return _date(services.get_date_layout(request.auth, organization_id, market_id, occurrence_id))


@router.put(_DATE, response={200: DateLayoutOut, 422: ErrorOut, **_ERRORS})
def save_date_layout(
    request, organization_id: int, market_id: int, occurrence_id: int, payload: DateLayoutIn
):
    """Choose the date's layout version and set an offer for every stall."""
    values = payload.model_dump()
    return _date(
        services.save_date_layout(request.auth, organization_id, market_id, occurrence_id, **values)
    )


@router.post(f"{_DATE}/publish", response={200: DateLayoutOut, 422: ErrorOut, **_ERRORS})
def publish(request, organization_id: int, market_id: int, occurrence_id: int, payload: PublishIn):
    return _date(
        services.publish_date_layout(
            request.auth,
            organization_id,
            market_id,
            occurrence_id,
            expected_revision=payload.expected_revision,
        )
    )


@router.post(f"{_DATE}/unpublish", response={200: DateLayoutOut, **_ERRORS})
def unpublish(request, organization_id: int, market_id: int, occurrence_id: int):
    return _date(
        services.unpublish_date_layout(request.auth, organization_id, market_id, occurrence_id)
    )


# --- Public -------------------------------------------------------------------------------


@public_router.get(
    "/occurrences/{occurrence_id}/layout", response={200: PublicLayoutOut, 404: ErrorOut}
)
def public_layout(request, occurrence_id: int):
    """Published layout and prices for a public, scheduled date. Prices are the
    organizer's listed prices; a listed stall isn't a promise of availability."""
    occurrence_layout, stalls, offers = services.public_date_layout(occurrence_id)
    version = occurrence_layout.layout_version
    currency = next(iter(offers.values())).currency
    items = []
    for stall in stalls:
        offer = offers.get(stall.pk)
        offered = bool(offer and offer.enabled)
        items.append(
            _stall(stall)
            | {
                "offer_id": offer.pk if offered else None,
                "offered": offered,
                "price_minor": offer.price_minor if offered else None,
            }
        )
    return {
        "occurrence_id": occurrence_layout.occurrence_id,
        "market_id": occurrence_layout.occurrence.market_id,
        "canvas_width": version.canvas_width,
        "canvas_height": version.canvas_height,
        "currency": currency,
        "currency_exponent": exponent(currency),
        "stalls": items,
    }
