"""Market endpoints: organizer management under
/api/v1/organizations/{id}/markets and anonymous reads under /api/v1/public."""

from datetime import date, datetime
from typing import Literal

from ninja import Router, Status

from core.auth import session_auth
from core.pagination import paginate
from core.schemas import ErrorOut
from markets import discovery, public, recurrence, services
from markets.models import EventOccurrence, MarketStatus
from markets.schemas import (
    CancellationPolicyIn,
    DiscoveryPage,
    MapResult,
    MarketCreateIn,
    MarketOut,
    MarketPage,
    MarketUpdateIn,
    OccurrenceCancelIn,
    OccurrenceCreateIn,
    OccurrenceOut,
    OccurrencePage,
    OccurrenceUpdateIn,
    PublicMarketOut,
    PublicOccurrenceDetailOut,
    PublicOccurrencePage,
    SeriesCreateIn,
    SeriesGeneratedOut,
    SeriesOut,
)

router = Router(tags=["markets"], auth=session_auth)
public_router = Router(tags=["public"])

_ERRORS = {400: ErrorOut, 401: ErrorOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut}
_BASE = "/{organization_id}/markets"


def occurrence_dict(occurrence, *, public_view=False) -> dict:
    zone = services.zone_for(occurrence.market)
    local_start = occurrence.starts_at.astimezone(zone)
    data = {
        "id": occurrence.pk,
        "market_id": occurrence.market_id,
        "starts_at": occurrence.starts_at,
        "ends_at": occurrence.ends_at,
        "timezone": occurrence.market.timezone,
        "local_date": local_start.date(),
        "local_start_time": local_start.time().replace(tzinfo=None),
        "local_end_time": occurrence.ends_at.astimezone(zone).time().replace(tzinfo=None),
        "status": occurrence.status,
        "cancellation_message": occurrence.cancellation_message,
    }
    if not public_view:
        data |= {
            "cancelled_at": occurrence.cancelled_at,
            "series_id": occurrence.series_id,
            "created_at": occurrence.created_at,
            "updated_at": occurrence.updated_at,
        }
    return data


def public_market_dict(market) -> dict:
    fields = PublicMarketOut.model_fields.keys() - {"organizer"}
    return {f: getattr(market, f) for f in fields} | {
        "organizer": {"name": market.organization.name}
    }


def _series_dict(series) -> dict:
    return {
        "id": series.pk,
        "market_id": series.market_id,
        "interval_weeks": series.interval_weeks,
        "weekdays": series.weekdays,
        "start_date": series.start_date,
        "end_date": series.end_date,
        "local_start_time": series.local_start_time,
        "local_end_time": series.local_end_time,
        "timezone": series.timezone,
        "created_by_user_id": series.created_by_id,
        "created_at": series.created_at,
        "occurrence_count": series.occurrences.count(),
    }


# --- Organizer: markets -----------------------------------------------------------------


@router.post(_BASE, response={201: MarketOut, 422: ErrorOut, **_ERRORS})
def create_market(request, organization_id: int, payload: MarketCreateIn):
    return Status(201, services.create_market(request.auth, organization_id, **payload.dict()))


@router.get(_BASE, response={200: MarketPage, 422: ErrorOut, **_ERRORS})
def list_markets(
    request,
    organization_id: int,
    status: MarketStatus | None = None,
    cursor: int | None = None,
    limit: int | None = None,
):
    markets = services.list_markets(request.auth, organization_id, status=status)
    items, next_cursor = paginate(markets, cursor=cursor, limit=limit)
    return {"items": items, "next_cursor": next_cursor}


@router.get(_BASE + "/{market_id}", response={200: MarketOut, **_ERRORS})
def get_market(request, organization_id: int, market_id: int):
    return services.get_market(request.auth, organization_id, market_id)


@router.patch(_BASE + "/{market_id}", response={200: MarketOut, 422: ErrorOut, **_ERRORS})
def update_market(request, organization_id: int, market_id: int, payload: MarketUpdateIn):
    changes = payload.dict(exclude_unset=True)
    return services.update_market(request.auth, organization_id, market_id, **changes)


@router.put(
    _BASE + "/{market_id}/cancellation-policy",
    response={200: MarketOut, 422: ErrorOut, **_ERRORS},
)
def set_cancellation_policy(
    request, organization_id: int, market_id: int, payload: CancellationPolicyIn
):
    """Vendor cancellation cutoff for new holds. Existing holds and bookings
    keep the terms they were offered."""
    return services.set_cancellation_policy(
        request.auth,
        organization_id,
        market_id,
        vendor_cutoff_hours=payload.vendor_cancellation_cutoff_hours,
    )


@router.post(_BASE + "/{market_id}/publish", response={200: MarketOut, **_ERRORS})
def publish_market(request, organization_id: int, market_id: int):
    return services.publish_market(request.auth, organization_id, market_id)


@router.post(_BASE + "/{market_id}/archive", response={200: MarketOut, **_ERRORS})
def archive_market(request, organization_id: int, market_id: int):
    return services.archive_market(request.auth, organization_id, market_id)


# --- Organizer: occurrences ----------------------------------------------------------------


@router.get(_BASE + "/{market_id}/occurrences", response={200: OccurrencePage, **_ERRORS})
def list_occurrences(
    request,
    organization_id: int,
    market_id: int,
    series_id: int | None = None,
    cursor: datetime | None = None,
    limit: int | None = None,
):
    occurrences = services.list_occurrences(
        request.auth, organization_id, market_id, series_id=series_id
    ).select_related("market")
    items, next_cursor = paginate(occurrences, cursor=cursor, limit=limit, key="starts_at")
    return {"items": [occurrence_dict(o) for o in items], "next_cursor": next_cursor}


@router.post(
    _BASE + "/{market_id}/occurrences", response={201: OccurrenceOut, 422: ErrorOut, **_ERRORS}
)
def create_occurrence(request, organization_id: int, market_id: int, payload: OccurrenceCreateIn):
    occurrence = services.create_occurrence(
        request.auth, organization_id, market_id, **payload.dict()
    )
    return Status(201, occurrence_dict(occurrence))


@router.patch(
    _BASE + "/{market_id}/occurrences/{occurrence_id}",
    response={200: OccurrenceOut, 422: ErrorOut, **_ERRORS},
)
def update_occurrence(
    request, organization_id: int, market_id: int, occurrence_id: int, payload: OccurrenceUpdateIn
):
    occurrence = services.update_occurrence(
        request.auth, organization_id, market_id, occurrence_id, **payload.dict(exclude_unset=True)
    )
    return occurrence_dict(occurrence)


@router.post(
    _BASE + "/{market_id}/occurrences/{occurrence_id}/cancel",
    response={200: OccurrenceOut, 422: ErrorOut, **_ERRORS},
)
def cancel_occurrence(
    request, organization_id: int, market_id: int, occurrence_id: int, payload: OccurrenceCancelIn
):
    occurrence = services.cancel_occurrence(
        request.auth, organization_id, market_id, occurrence_id, message=payload.message
    )
    return occurrence_dict(occurrence)


# --- Organizer: recurrence series ------------------------------------------------------------


@router.post(
    _BASE + "/{market_id}/series",
    response={201: SeriesGeneratedOut, 200: SeriesGeneratedOut, 422: ErrorOut, **_ERRORS},
)
def create_series(request, organization_id: int, market_id: int, payload: SeriesCreateIn):
    rule = recurrence.WeeklyRule(
        interval_weeks=payload.interval_weeks,
        weekdays=tuple(payload.weekdays),
        start_date=payload.start_date,
        end_date=payload.end_date,
        local_start_time=payload.local_start_time,
        local_end_time=payload.local_end_time,
    )
    result = services.create_series(request.auth, organization_id, market_id, rule=rule)
    body = {
        "series": _series_dict(result.series),
        "created_occurrences": result.created,
        "already_existed": result.already_existed,
    }
    return Status(200 if result.already_existed else 201, body)


@router.get(_BASE + "/{market_id}/series/{series_id}", response={200: SeriesOut, **_ERRORS})
def get_series(request, organization_id: int, market_id: int, series_id: int):
    return _series_dict(services.get_series(request.auth, organization_id, market_id, series_id))


# --- Public ------------------------------------------------------------------------------------


def _discovery_occurrence(occurrence: EventOccurrence, market) -> dict:
    occurrence.market = market
    data = occurrence_dict(occurrence, public_view=True)
    return {
        k: data[k]
        for k in (
            "id",
            "starts_at",
            "ends_at",
            "timezone",
            "local_date",
            "local_start_time",
            "local_end_time",
        )
    }


def _filters(request_params: dict) -> discovery.Filters:
    return discovery.build_filters(**request_params)


@public_router.get("/markets", response={200: DiscoveryPage, 400: ErrorOut, 422: ErrorOut})
def discover_markets(
    request,
    q: str | None = None,
    market_type: Literal["FARMERS_MARKET", "POPUP"] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    south: float | None = None,
    west: float | None = None,
    north: float | None = None,
    east: float | None = None,
    lat: float | None = None,
    lng: float | None = None,
    radius_km: float | None = None,
    cursor: int | None = None,
    limit: int | None = None,
):
    """Search published markets with upcoming scheduled dates. One item per
    market. ``cursor`` is an offset returned as ``next_cursor``."""
    filters = _filters(
        dict(
            q=q,
            market_type=market_type,
            date_from=date_from,
            date_to=date_to,
            south=south,
            west=west,
            north=north,
            east=east,
            lat=lat,
            lng=lng,
            radius_km=radius_km,
        )
    )
    markets, previews, next_cursor = discovery.search_page(filters, offset=cursor or 0, limit=limit)
    items = []
    for market in markets:
        preview = previews.get(market.pk, [])
        items.append(
            {
                **{
                    f: getattr(market, f)
                    for f in (
                        "id",
                        "name",
                        "market_type",
                        "venue_name",
                        "city",
                        "region",
                        "country",
                        "latitude",
                        "longitude",
                        "timezone",
                    )
                },
                "distance_km": getattr(market, "distance_km", None),
                "next_occurrence": _discovery_occurrence(preview[0], market),
                "upcoming_preview": [_discovery_occurrence(o, market) for o in preview],
            }
        )
    return {"items": items, "next_cursor": next_cursor}


@public_router.get("/markets/map", response={200: MapResult, 400: ErrorOut, 422: ErrorOut})
def discover_map(
    request,
    south: float | None = None,
    west: float | None = None,
    north: float | None = None,
    east: float | None = None,
    q: str | None = None,
    market_type: Literal["FARMERS_MARKET", "POPUP"] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    lat: float | None = None,
    lng: float | None = None,
    radius_km: float | None = None,
):
    """Markers for markets with coordinates in an area, same filters as the
    list. At most MAP_MAX_MARKERS; ``truncated`` says the client should zoom
    in or narrow the search."""
    filters = _filters(
        dict(
            q=q,
            market_type=market_type,
            date_from=date_from,
            date_to=date_to,
            south=south,
            west=west,
            north=north,
            east=east,
            lat=lat,
            lng=lng,
            radius_km=radius_km,
        )
    )
    markets, total, truncated = discovery.map_markers(filters)
    next_by_id = EventOccurrence.objects.in_bulk([m.next_occurrence_id for m in markets])
    items = [
        {
            "id": m.pk,
            "name": m.name,
            "market_type": m.market_type,
            "city": m.city,
            "region": m.region,
            "latitude": m.latitude,
            "longitude": m.longitude,
            "distance_km": getattr(m, "distance_km", None),
            "next_occurrence": _discovery_occurrence(next_by_id[m.next_occurrence_id], m),
        }
        for m in markets
    ]
    return {
        "items": items,
        "total": total,
        "truncated": truncated,
        "limit": discovery.MAP_MAX_MARKERS,
    }


@public_router.get("/markets/{market_id}", response={200: PublicMarketOut, 404: ErrorOut})
def public_market(request, market_id: int):
    return public_market_dict(public.get_market(market_id))


@public_router.get(
    "/markets/{market_id}/occurrences", response={200: PublicOccurrencePage, 404: ErrorOut}
)
def public_market_occurrences(
    request, market_id: int, cursor: datetime | None = None, limit: int | None = None
):
    occurrences = public.upcoming_occurrences(market_id).select_related("market")
    items, next_cursor = paginate(occurrences, cursor=cursor, limit=limit, key="starts_at")
    return {
        "items": [occurrence_dict(o, public_view=True) for o in items],
        "next_cursor": next_cursor,
    }


@public_router.get(
    "/occurrences/{occurrence_id}", response={200: PublicOccurrenceDetailOut, 404: ErrorOut}
)
def public_occurrence(request, occurrence_id: int):
    occurrence = public.get_occurrence(occurrence_id)
    return occurrence_dict(occurrence, public_view=True) | {
        "market": public_market_dict(occurrence.market)
    }
