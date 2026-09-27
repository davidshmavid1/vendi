"""Organization moderation endpoints, under /api/v1/organizations/{id}/restrictions.
OWNER/ADMIN only (checked in moderation/services.py); CSRF on mutations."""

from typing import Literal

from ninja import Router, Status

from core.auth import session_auth
from core.pagination import paginate
from core.schemas import ErrorOut
from moderation import services
from moderation.schemas import (
    RestrictionCreateIn,
    RestrictionOut,
    RestrictionPage,
    RestrictionRevokeIn,
)

router = Router(tags=["moderation"], auth=session_auth)

_ERRORS = {400: ErrorOut, 401: ErrorOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut}


def _out(restriction) -> dict:
    return {
        "id": restriction.pk,
        "target_type": restriction.target_type,
        "account_id": restriction.account_id,
        "vendor_business_id": restriction.vendor_business_id,
        "status": restriction.status(),
        "reason": restriction.reason,
        "created_by_user_id": restriction.created_by_id,
        "created_at": restriction.created_at,
        "expires_at": restriction.expires_at,
        "revoked_at": restriction.revoked_at,
        "revoked_by_user_id": restriction.revoked_by_id,
        "revocation_note": restriction.revocation_note,
    }


@router.post(
    "/{organization_id}/restrictions",
    response={201: RestrictionOut, 422: ErrorOut, **_ERRORS},
)
def create_restriction(request, organization_id: int, payload: RestrictionCreateIn):
    restriction = services.create_restriction(request.auth, organization_id, **payload.dict())
    return Status(201, _out(restriction))


@router.get(
    "/{organization_id}/restrictions", response={200: RestrictionPage, 422: ErrorOut, **_ERRORS}
)
def list_restrictions(
    request,
    organization_id: int,
    status: Literal["effective", "expired", "revoked"] | None = None,
    target_type: Literal["account", "vendor_business"] | None = None,
    cursor: int | None = None,
    limit: int | None = None,
):
    restrictions = services.list_restrictions(
        request.auth, organization_id, status=status, target_type=target_type
    )
    items, next_cursor = paginate(restrictions, cursor=cursor, limit=limit)
    return {"items": [_out(r) for r in items], "next_cursor": next_cursor}


@router.get(
    "/{organization_id}/restrictions/{restriction_id}", response={200: RestrictionOut, **_ERRORS}
)
def get_restriction(request, organization_id: int, restriction_id: int):
    return _out(services.get_restriction(request.auth, organization_id, restriction_id))


@router.post(
    "/{organization_id}/restrictions/{restriction_id}/revoke",
    response={200: RestrictionOut, 422: ErrorOut, **_ERRORS},
)
def revoke_restriction(
    request, organization_id: int, restriction_id: int, payload: RestrictionRevokeIn
):
    restriction = services.revoke_restriction(
        request.auth, organization_id, restriction_id, note=payload.note
    )
    return _out(restriction)
