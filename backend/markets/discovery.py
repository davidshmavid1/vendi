"""Public market discovery: one query path shared by list and map results.

Eligible market: status PUBLISHED with at least one *matching occurrence*,
i.e. an occurrence that is SCHEDULED, has not ended, and (when a date range
is given) starts on a local calendar date within [date_from, date_to] in the
market's own timezone.

Geography uses plain PostgreSQL columns (no PostGIS): a latitude/longitude
bounding-box prefilter, then a great-circle (haversine) distance computed in
SQL, in kilometers. Markets without coordinates match non-geographic searches
only.
"""

import math
from dataclasses import dataclass
from datetime import date

from django.db.models import (
    DateField,
    F,
    FloatField,
    Func,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Value,
    Window,
)
from django.db.models.functions import (
    ASin,
    Cast,
    Cos,
    Least,
    Power,
    Radians,
    RowNumber,
    Sin,
    Sqrt,
)
from django.utils import timezone

from core.exceptions import InvalidRequest
from markets.models import EventOccurrence, Market, MarketStatus, OccurrenceStatus

EARTH_RADIUS_KM = 6371.0088
KM_PER_DEGREE_LAT = 111.32
MAX_RADIUS_KM = 500
MAX_QUERY_LENGTH = 100
MAX_DATE_SPAN_DAYS = 366
PREVIEW_DATES = 3
LIST_DEFAULT_LIMIT, LIST_MAX_LIMIT = 20, 50
MAP_MAX_MARKERS = 300


@dataclass(frozen=True)
class BBox:
    south: float
    west: float
    north: float
    east: float  # east < west means the box crosses the antimeridian


@dataclass(frozen=True)
class Near:
    latitude: float
    longitude: float
    radius_km: float


@dataclass(frozen=True)
class Filters:
    q: str = ""
    market_type: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    bbox: BBox | None = None
    near: Near | None = None


def _coordinate(value: float, low: float, high: float, name: str) -> float:
    if value is None or not math.isfinite(value) or not low <= value <= high:
        raise InvalidRequest(
            f"{name} must be between {low} and {high}.", code="coordinates_invalid"
        )
    return float(value)


def build_filters(
    *,
    q: str | None = None,
    market_type: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    south: float | None = None,
    west: float | None = None,
    north: float | None = None,
    east: float | None = None,
    lat: float | None = None,
    lng: float | None = None,
    radius_km: float | None = None,
) -> Filters:
    q = (q or "").strip()
    if len(q) > MAX_QUERY_LENGTH:
        raise InvalidRequest("Search text is too long.", code="query_invalid")
    if date_from and date_to:
        if date_to < date_from:
            raise InvalidRequest("date_to is before date_from.", code="date_range_invalid")
        if (date_to - date_from).days > MAX_DATE_SPAN_DAYS:
            raise InvalidRequest(
                f"A date range may span at most {MAX_DATE_SPAN_DAYS} days.",
                code="date_range_invalid",
            )

    bbox = None
    box_parts = (south, west, north, east)
    if any(p is not None for p in box_parts):
        if any(p is None for p in box_parts):
            raise InvalidRequest("Give south, west, north and east together.", code="bbox_invalid")
        bbox = BBox(
            south=_coordinate(south, -90, 90, "south"),
            west=_coordinate(west, -180, 180, "west"),
            north=_coordinate(north, -90, 90, "north"),
            east=_coordinate(east, -180, 180, "east"),
        )
        if bbox.south > bbox.north:
            raise InvalidRequest("south must not be above north.", code="bbox_invalid")

    near = None
    near_parts = (lat, lng, radius_km)
    if any(p is not None for p in near_parts):
        if any(p is None for p in near_parts):
            raise InvalidRequest("Give lat, lng and radius_km together.", code="near_invalid")
        if not math.isfinite(radius_km) or not 0 < radius_km <= MAX_RADIUS_KM:
            raise InvalidRequest(
                f"radius_km must be above 0 and at most {MAX_RADIUS_KM}.", code="radius_invalid"
            )
        near = Near(
            latitude=_coordinate(lat, -90, 90, "lat"),
            longitude=_coordinate(lng, -180, 180, "lng"),
            radius_km=float(radius_km),
        )
    return Filters(q, market_type, date_from, date_to, bbox, near)


# --- Query building --------------------------------------------------------------------


def _longitude_q(west: float, east: float) -> Q:
    if west <= east:
        return Q(longitude__gte=west, longitude__lte=east)
    return Q(longitude__gte=west) | Q(longitude__lte=east)  # crosses ±180°


def _bbox_q(box: BBox) -> Q:
    return Q(latitude__gte=box.south, latitude__lte=box.north) & _longitude_q(box.west, box.east)


def _near_prefilter_q(near: Near) -> Q:
    """A box that contains the search circle, so the distance is only
    computed for nearby candidates. Near a pole every longitude qualifies."""
    dlat = near.radius_km / KM_PER_DEGREE_LAT
    south, north = near.latitude - dlat, near.latitude + dlat
    q = Q(latitude__gte=max(south, -90), latitude__lte=min(north, 90))
    if south <= -90 or north >= 90:
        return q
    dlng = math.degrees(
        math.asin(
            min(
                1.0,
                math.sin(near.radius_km / EARTH_RADIUS_KM) / math.cos(math.radians(near.latitude)),
            )
        )
    )
    if dlng >= 180:
        return q
    west, east = near.longitude - dlng, near.longitude + dlng
    wrap = lambda x: ((x + 180) % 360) - 180  # noqa: E731
    return q & _longitude_q(wrap(west) if west < -180 else west, wrap(east) if east > 180 else east)


def _distance_km(near: Near):
    lat1, lng1 = math.radians(near.latitude), math.radians(near.longitude)
    lat2 = Radians(Cast(F("latitude"), FloatField()))
    lng2 = Radians(Cast(F("longitude"), FloatField()))
    haversine = Power(Sin((lat2 - Value(lat1)) / 2), 2) + Value(math.cos(lat1)) * Cos(lat2) * Power(
        Sin((lng2 - Value(lng1)) / 2), 2
    )
    return Value(2 * EARTH_RADIUS_KM) * ASin(Sqrt(Least(haversine, Value(1.0))))


def matching_occurrences(filters: Filters, now=None) -> QuerySet:
    occurrences = EventOccurrence.objects.filter(
        status=OccurrenceStatus.SCHEDULED, ends_at__gt=now or timezone.now()
    )
    if filters.date_from or filters.date_to:
        # Local calendar date of the start, in each market's own timezone.
        local_date = Cast(
            Func(F("market__timezone"), F("starts_at"), function="timezone"),
            output_field=DateField(),
        )
        occurrences = occurrences.annotate(local_date=local_date)
        if filters.date_from:
            occurrences = occurrences.filter(local_date__gte=filters.date_from)
        if filters.date_to:
            occurrences = occurrences.filter(local_date__lte=filters.date_to)
    return occurrences


def eligible_markets(filters: Filters, now=None) -> QuerySet:
    """Markets for both list and map, annotated with their next matching
    occurrence and (for nearby searches) ``distance_km``. Deterministically
    ordered: distance (nearby only), next start, then id."""
    occurrences = matching_occurrences(filters, now)
    next_matching = occurrences.filter(market=OuterRef("pk")).order_by("starts_at", "pk")
    markets = (
        Market.objects.filter(status=MarketStatus.PUBLISHED)
        .annotate(
            next_starts_at=Subquery(next_matching.values("starts_at")[:1]),
            next_occurrence_id=Subquery(next_matching.values("pk")[:1]),
        )
        .filter(next_occurrence_id__isnull=False)
    )
    if filters.q:
        markets = markets.filter(
            Q(name__icontains=filters.q)
            | Q(venue_name__icontains=filters.q)
            | Q(city__icontains=filters.q)
            | Q(region__icontains=filters.q)
        )
    if filters.market_type:
        markets = markets.filter(market_type=filters.market_type)
    if filters.bbox:
        markets = markets.filter(_bbox_q(filters.bbox))
    ordering = ["next_starts_at", "pk"]
    if filters.near:
        markets = (
            markets.filter(_near_prefilter_q(filters.near))
            .annotate(distance_km=_distance_km(filters.near))
            .filter(distance_km__lte=filters.near.radius_km)
        )
        ordering = ["distance_km", *ordering]
    return markets.order_by(*ordering)


def preview_occurrences(filters: Filters, market_ids: list[int], now=None) -> dict[int, list]:
    """Up to PREVIEW_DATES matching occurrences per market, in one query."""
    rows = (
        matching_occurrences(filters, now)
        .filter(market_id__in=market_ids)
        .select_related("market")
        .annotate(
            position=Window(
                RowNumber(), partition_by=[F("market_id")], order_by=[F("starts_at"), F("pk")]
            )
        )
        .filter(position__lte=PREVIEW_DATES)
        .order_by("market_id", "starts_at", "pk")
    )
    grouped: dict[int, list] = {market_id: [] for market_id in market_ids}
    for occurrence in rows:
        grouped[occurrence.market_id].append(occurrence)
    return grouped


def search_page(filters: Filters, *, offset: int, limit: int | None):
    limit = min(max(limit or LIST_DEFAULT_LIMIT, 1), LIST_MAX_LIMIT)
    offset = max(offset, 0)
    now = timezone.now()
    markets = list(eligible_markets(filters, now)[offset : offset + limit + 1])
    has_more = len(markets) > limit
    markets = markets[:limit]
    previews = preview_occurrences(filters, [m.pk for m in markets], now)
    return markets, previews, (offset + limit if has_more else None)


def map_markers(filters: Filters):
    if filters.bbox is None and filters.near is None:
        raise InvalidRequest(
            "Map results need an area: give south/west/north/east or lat/lng/radius_km.",
            code="bbox_invalid",
        )
    markets = eligible_markets(filters).filter(latitude__isnull=False, longitude__isnull=False)
    items = list(markets[: MAP_MAX_MARKERS + 1])
    truncated = len(items) > MAP_MAX_MARKERS
    total = markets.count() if truncated else len(items)
    return items[:MAP_MAX_MARKERS], total, truncated
