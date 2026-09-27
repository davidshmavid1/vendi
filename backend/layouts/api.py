"""Stall layout endpoints. Handlers only translate HTTP; rules live in
layouts/services.py.

- Organizer: /api/v1/organizations/{org}/markets/{market}/occurrences/{occ}/layout
- Public:    /api/v1/public/occurrences/{occ}/layout
"""

from ninja import Router

from core.auth import session_auth
from core.schemas import ErrorOut
from layouts import services
from layouts.money import exponent
from layouts.schemas import LayoutIn, LayoutOut, PublicLayoutOut, PublishIn

router = Router(tags=["layouts"], auth=session_auth)
public_router = Router(tags=["public"])

_ERRORS = {400: ErrorOut, 401: ErrorOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut}
_PATH = "/{organization_id}/markets/{market_id}/occurrences/{occurrence_id}/layout"


def _stall(stall) -> dict:
    return {
        "id": stall.pk,
        "label": stall.label,
        "description": stall.description,
        "x": stall.x,
        "y": stall.y,
        "width": stall.width,
        "height": stall.height,
        "physical_width": stall.physical_width,
        "physical_depth": stall.physical_depth,
        "physical_unit": stall.physical_unit or None,
        "enabled": stall.enabled,
        "price_minor": stall.price_minor,
        "created_at": stall.created_at,
        "updated_at": stall.updated_at,
    }


def _layout(result) -> dict:
    occurrence, layout, stalls = result
    return {
        "id": layout.pk,
        "occurrence_id": occurrence.pk,
        "market_id": occurrence.market_id,
        "canvas_width": layout.canvas_width,
        "canvas_height": layout.canvas_height,
        "currency": layout.currency,
        "currency_exponent": exponent(layout.currency),
        "revision": layout.revision,
        "published": layout.is_published,
        "published_at": layout.published_at,
        "created_at": layout.created_at,
        "updated_at": layout.updated_at,
        "stalls": [_stall(s) for s in stalls],
    }


@router.get(_PATH, response={200: LayoutOut, **_ERRORS})
def get_layout(request, organization_id: int, market_id: int, occurrence_id: int):
    return _layout(services.get_layout(request.auth, organization_id, market_id, occurrence_id))


@router.put(_PATH, response={200: LayoutOut, 422: ErrorOut, **_ERRORS})
def save_layout(
    request, organization_id: int, market_id: int, occurrence_id: int, payload: LayoutIn
):
    """Create or replace the layout atomically. Send every existing stall."""
    values = payload.model_dump()
    return _layout(
        services.save_layout(request.auth, organization_id, market_id, occurrence_id, **values)
    )


@router.post(f"{_PATH}/publish", response={200: LayoutOut, 422: ErrorOut, **_ERRORS})
def publish(request, organization_id: int, market_id: int, occurrence_id: int, payload: PublishIn):
    return _layout(
        services.publish_layout(
            request.auth,
            organization_id,
            market_id,
            occurrence_id,
            expected_revision=payload.expected_revision,
        )
    )


@router.post(f"{_PATH}/unpublish", response={200: LayoutOut, **_ERRORS})
def unpublish(request, organization_id: int, market_id: int, occurrence_id: int):
    return _layout(
        services.unpublish_layout(request.auth, organization_id, market_id, occurrence_id)
    )


@public_router.get(
    "/occurrences/{occurrence_id}/layout", response={200: PublicLayoutOut, 404: ErrorOut}
)
def public_layout(request, occurrence_id: int):
    """Published layout for a public, scheduled date. Prices are the
    organizer's listed prices; a listed stall isn't a promise of availability."""
    layout, stalls = services.public_layout(occurrence_id)
    return {
        "occurrence_id": layout.occurrence_id,
        "market_id": layout.occurrence.market_id,
        "canvas_width": layout.canvas_width,
        "canvas_height": layout.canvas_height,
        "currency": layout.currency,
        "currency_exponent": exponent(layout.currency),
        "stalls": [
            {
                "id": s.pk,
                "label": s.label,
                "description": s.description,
                "x": s.x,
                "y": s.y,
                "width": s.width,
                "height": s.height,
                "physical_width": s.physical_width,
                "physical_depth": s.physical_depth,
                "physical_unit": s.physical_unit or None,
                "offered": s.enabled,
                "price_minor": s.price_minor,
            }
            for s in stalls
        ],
    }
