"""Stall hold endpoints. Handlers only translate HTTP; rules live in
reservations/services.py. There is deliberately no endpoint that confirms a
reservation directly: payments (payments/api.py) confirms one only after a
verified payment, or for a free stall.

- Vendor: /api/v1/vendors/{business_id}/reservations[/{id}[/release]]
- Public: /api/v1/public/occurrences/{id}/stall-availability
"""

from django.conf import settings
from django.utils import timezone
from ninja import Router, Status

from accounts.throttles import UserThrottle
from core.auth import session_auth
from core.pagination import paginate
from core.schemas import ErrorOut
from layouts.money import exponent
from reservations import services
from reservations.models import Reservation
from reservations.schemas import (
    AvailabilityOut,
    HoldIn,
    ReservationOut,
    ReservationPage,
    ReservationStatusName,
)

router = Router(tags=["reservations"], auth=session_auth)
public_router = Router(tags=["public"])

_ERRORS = {400: ErrorOut, 401: ErrorOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut}
_RATES = settings.RESERVATION_RATE_LIMITS


def reservation_out(reservation: Reservation) -> dict:
    now = timezone.now()
    occurrence = reservation.occurrence
    return {
        "id": reservation.pk,
        "status": reservation.status_at(now),
        "offer_id": reservation.offer_id,
        "stall": {"id": reservation.offer.stall_id, "label": reservation.offer.stall.label},
        "occurrence": {
            "id": occurrence.pk,
            "market_id": occurrence.market_id,
            "market_name": occurrence.market.name,
            "starts_at": occurrence.starts_at,
            "ends_at": occurrence.ends_at,
            "timezone": occurrence.market.timezone,
        },
        "application_id": reservation.application_id,
        "price_minor": reservation.price_minor,
        "currency": reservation.currency,
        "currency_exponent": exponent(reservation.currency),
        "expires_at": reservation.expires_at,
        "created_at": reservation.created_at,
        "expired_at": reservation.expired_at
        or (reservation.expires_at if reservation.status_at(now) == "EXPIRED" else None),
        "released_at": reservation.released_at,
        "confirmed_at": reservation.confirmed_at,
        "payment_pending": reservation.payment_pending,
        "server_time": now,
    }


@router.post(
    "/{business_id}/reservations",
    response={200: ReservationOut, 201: ReservationOut, 422: ErrorOut, 429: ErrorOut, **_ERRORS},
    throttle=[UserThrottle("hold_user", _RATES)],
)
def hold(request, business_id: int, payload: HoldIn):
    """Hold a stall for a few minutes. 201 for a new hold; 200 when the same
    request_key is sent again for the same stall (the original reservation,
    in whatever state it's in now)."""
    result = services.acquire_hold(
        request.auth, business_id, offer_id=payload.offer_id, request_key=payload.request_key
    )
    reservation = services.get_for_business(request.auth, business_id, result.reservation.pk)
    return Status(201 if result.created else 200, reservation_out(reservation))


@router.get("/{business_id}/reservations", response={200: ReservationPage, **_ERRORS})
def list_reservations(
    request,
    business_id: int,
    occurrence_id: int | None = None,
    status: ReservationStatusName | None = None,
    cursor: int | None = None,
    limit: int | None = None,
):
    queryset = services.list_for_business(
        request.auth, business_id, occurrence_id=occurrence_id, status=status
    )
    items, next_cursor = paginate(queryset, cursor=cursor, limit=limit)
    return {"items": [reservation_out(r) for r in items], "next_cursor": next_cursor}


@router.get(
    "/{business_id}/reservations/{reservation_id}", response={200: ReservationOut, **_ERRORS}
)
def get_reservation(request, business_id: int, reservation_id: int):
    return reservation_out(services.get_for_business(request.auth, business_id, reservation_id))


@router.post(
    "/{business_id}/reservations/{reservation_id}/release",
    response={200: ReservationOut, **_ERRORS},
)
def release(request, business_id: int, reservation_id: int):
    services.release_hold(request.auth, business_id, reservation_id)
    return reservation_out(services.get_for_business(request.auth, business_id, reservation_id))


@public_router.get(
    "/occurrences/{occurrence_id}/stall-availability",
    response={200: AvailabilityOut, 404: ErrorOut},
)
def availability(request, occurrence_id: int):
    """Whether each stall of a published layout is available right now.
    Never says who holds a stall."""
    return {"occurrence_id": occurrence_id, "items": services.public_availability(occurrence_id)}
