"""Market and schedule operations for organizers.

OWNER/ADMIN manage markets; STAFF read. Every operation re-checks the
caller's current organization membership. Operations that create or move
occurrences lock the market row first, so concurrent schedule changes for
one market run one at a time.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo, available_timezones

from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import User
from core.exceptions import Conflict, InvalidRequest, NotFound
from markets import recurrence
from markets.models import (
    MAX_CANCELLATION_CUTOFF_HOURS,
    EventOccurrence,
    Market,
    MarketStatus,
    MarketType,
    OccurrenceStatus,
    RecurrenceSeries,
)
from markets.signals import occurrence_cancelled
from organizations.models import Role
from organizations.permissions import membership_for, require_role

EDITABLE_FIELDS = (
    "name",
    "description",
    "market_type",
    "venue_name",
    "address_line1",
    "address_line2",
    "city",
    "region",
    "postal_code",
    "country",
    "latitude",
    "longitude",
    "timezone",
)
# Must be non-blank to publish, and to stay published (DB CHECK for venue).
PUBLISH_REQUIRED_FIELDS = ("name", "venue_name", "address_line1", "city", "country", "timezone")
COUNTRY_PATTERN = re.compile(r"[A-Z]{2}")
_TIMEZONES = frozenset(available_timezones())


def _manager(actor: User, organization_id: int):
    membership = membership_for(actor, organization_id)
    require_role(membership, Role.OWNER, Role.ADMIN)
    return membership


def _market_in(organization_id: int, market_id: int, *, lock=False) -> Market:
    queryset = Market.objects.filter(pk=market_id, organization_id=organization_id)
    market = (queryset.select_for_update() if lock else queryset).first()
    if market is None:
        raise NotFound("Market not found.")
    return market


def _require_not_archived(market: Market) -> None:
    if market.status == MarketStatus.ARCHIVED:
        raise Conflict("Archived markets can't be changed.", code="market_archived")


def zone_for(market: Market) -> ZoneInfo:
    return ZoneInfo(market.timezone)


# --- Markets ------------------------------------------------------------------


def _clean_market(values: dict, existing: Market | None = None) -> dict:
    cleaned = {}
    for field, raw in values.items():
        if field not in EDITABLE_FIELDS:
            raise InvalidRequest(f"{field} cannot be set.", code="field_not_editable")
        if field in ("latitude", "longitude"):
            cleaned[field] = (
                None if raw is None else Decimal(str(raw)).quantize(Decimal("0.000001"))
            )
            continue
        value = (raw or "").strip()
        if field == "name" and not value:
            raise InvalidRequest("Enter a market name.", code="name_invalid")
        if field == "market_type" and value not in MarketType.values:
            raise InvalidRequest("Choose FARMERS_MARKET or POPUP.", code="market_type_invalid")
        if field == "country":
            value = value.upper()
            if value and not COUNTRY_PATTERN.fullmatch(value):
                raise InvalidRequest(
                    "Use a two-letter ISO country code, e.g. US.", code="country_invalid"
                )
        if field == "timezone" and value not in _TIMEZONES:
            raise InvalidRequest(
                "Use an IANA timezone name, e.g. America/Chicago.", code="timezone_invalid"
            )
        cleaned[field] = value

    lat = cleaned.get("latitude", existing.latitude if existing else None)
    lng = cleaned.get("longitude", existing.longitude if existing else None)
    if (lat is None) != (lng is None):
        raise InvalidRequest(
            "Give latitude and longitude together, or neither.", code="coordinates_invalid"
        )
    if lat is not None and not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise InvalidRequest(
            "Latitude must be -90..90 and longitude -180..180.", code="coordinates_invalid"
        )
    return cleaned


def create_market(actor: User, organization_id: int, **values) -> Market:
    cleaned = _clean_market(values)
    with transaction.atomic():
        membership = _manager(actor, organization_id)
        return Market.objects.create(organization=membership.organization, **cleaned)


def list_markets(actor: User, organization_id: int, *, status: str | None = None):
    membership_for(actor, organization_id)  # any member may read
    markets = Market.objects.filter(organization_id=organization_id)
    return markets.filter(status=status) if status else markets


def get_market(actor: User, organization_id: int, market_id: int) -> Market:
    membership_for(actor, organization_id)
    return _market_in(organization_id, market_id)


def _missing_for_publication(market: Market) -> list[str]:
    return [f for f in PUBLISH_REQUIRED_FIELDS if not getattr(market, f)]


def update_market(actor: User, organization_id: int, market_id: int, **values) -> Market:
    with transaction.atomic():
        _manager(actor, organization_id)
        market = _market_in(organization_id, market_id, lock=True)
        _require_not_archived(market)
        cleaned = _clean_market(values, existing=market)
        if (
            "timezone" in cleaned
            and cleaned["timezone"] != market.timezone
            and market.occurrences.exists()
        ):
            raise Conflict(
                "The timezone can't change once dates are scheduled; their local times "
                "would shift.",
                code="timezone_locked",
            )
        for field, value in cleaned.items():
            setattr(market, field, value)
        if market.status == MarketStatus.PUBLISHED and _missing_for_publication(market):
            raise InvalidRequest(
                "A published market must keep its venue and timezone details.",
                code="publication_requirements",
                details=[{"field": f} for f in _missing_for_publication(market)],
            )
        market.save()
    return market


def publish_market(actor: User, organization_id: int, market_id: int) -> Market:
    with transaction.atomic():
        _manager(actor, organization_id)
        market = _market_in(organization_id, market_id, lock=True)
        if market.status == MarketStatus.PUBLISHED:
            return market  # already published: nothing to do
        _require_not_archived(market)
        missing = [{"field": f} for f in _missing_for_publication(market)]
        if not market.occurrences.scheduled().upcoming().exists():
            missing.append({"field": "occurrences", "reason": "no upcoming scheduled date"})
        if missing:
            raise InvalidRequest(
                "This market isn't ready to publish.",
                code="publication_requirements",
                details=missing,
            )
        market.status = MarketStatus.PUBLISHED
        market.published_at = timezone.now()
        market.save(update_fields=["status", "published_at", "updated_at"])
    return market


def archive_market(actor: User, organization_id: int, market_id: int) -> Market:
    """Remove from public discovery for good. Records and dates are kept."""
    with transaction.atomic():
        _manager(actor, organization_id)
        market = _market_in(organization_id, market_id, lock=True)
        if market.status == MarketStatus.ARCHIVED:
            raise Conflict("This market is already archived.", code="market_archived")
        market.status = MarketStatus.ARCHIVED
        market.archived_at = timezone.now()
        market.save(update_fields=["status", "archived_at", "updated_at"])
    return market


# --- Occurrences --------------------------------------------------------------------


def _aware(value: datetime, field: str) -> datetime:
    if value is None or timezone.is_naive(value):
        raise InvalidRequest(
            f"{field} must include a UTC offset, e.g. 2026-10-03T08:00:00-05:00.",
            code="timestamp_invalid",
        )
    return value


def _check_times(starts_at: datetime, ends_at: datetime) -> None:
    if ends_at <= starts_at:
        raise InvalidRequest("ends_at must be after starts_at.", code="timestamp_invalid")


def list_occurrences(
    actor: User, organization_id: int, market_id: int, *, series_id: int | None = None
):
    membership_for(actor, organization_id)
    market = _market_in(organization_id, market_id)
    occurrences = market.occurrences.all()
    return occurrences.filter(series_id=series_id) if series_id else occurrences


def _occurrence_in(market: Market, occurrence_id: int) -> EventOccurrence:
    occurrence = (
        EventOccurrence.objects.select_for_update().filter(pk=occurrence_id, market=market).first()
    )
    if occurrence is None:
        raise NotFound("Event date not found.")
    return occurrence


def _save_unique(occurrence: EventOccurrence, **save_kwargs) -> EventOccurrence:
    try:
        with transaction.atomic():
            occurrence.save(**save_kwargs)
    except IntegrityError:
        raise Conflict(
            "This market already has an event starting at that time.", code="occurrence_exists"
        ) from None
    return occurrence


def create_occurrence(
    actor: User, organization_id: int, market_id: int, *, starts_at: datetime, ends_at: datetime
) -> EventOccurrence:
    starts_at, ends_at = _aware(starts_at, "starts_at"), _aware(ends_at, "ends_at")
    _check_times(starts_at, ends_at)
    with transaction.atomic():
        _manager(actor, organization_id)
        market = _market_in(organization_id, market_id, lock=True)
        _require_not_archived(market)
        return _save_unique(EventOccurrence(market=market, starts_at=starts_at, ends_at=ends_at))


def update_occurrence(
    actor: User,
    organization_id: int,
    market_id: int,
    occurrence_id: int,
    *,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> EventOccurrence:
    with transaction.atomic():
        _manager(actor, organization_id)
        market = _market_in(organization_id, market_id, lock=True)
        _require_not_archived(market)
        occurrence = _occurrence_in(market, occurrence_id)
        if occurrence.status == OccurrenceStatus.CANCELLED:
            raise Conflict("Cancelled dates can't be edited.", code="occurrence_cancelled")
        if starts_at is not None:
            occurrence.starts_at = _aware(starts_at, "starts_at")
        if ends_at is not None:
            occurrence.ends_at = _aware(ends_at, "ends_at")
        _check_times(occurrence.starts_at, occurrence.ends_at)
        return _save_unique(occurrence)


def cancel_occurrence(
    actor: User, organization_id: int, market_id: int, occurrence_id: int, *, message: str = ""
) -> EventOccurrence:
    """Mark the date cancelled, keeping the record. From this moment it
    accepts no applications, holds, checkouts or bookings (each checks the
    date's status under its lock). ``occurrence_cancelled`` is sent in the
    same transaction so bookings can record the cleanup of the date's holds,
    checkouts and bookings (processed afterwards, in batches)."""
    with transaction.atomic():
        membership = _manager(actor, organization_id)
        market = _market_in(organization_id, market_id, lock=True)
        _require_not_archived(market)
        occurrence = _occurrence_in(market, occurrence_id)
        if occurrence.status == OccurrenceStatus.CANCELLED:
            raise Conflict("This date is already cancelled.", code="occurrence_cancelled")
        occurrence.status = OccurrenceStatus.CANCELLED
        occurrence.cancelled_at = timezone.now()
        occurrence.cancellation_message = (message or "").strip()
        occurrence.save(
            update_fields=["status", "cancelled_at", "cancellation_message", "updated_at"]
        )
        occurrence_cancelled.send(
            sender=EventOccurrence, occurrence=occurrence, actor=actor, role=membership.role
        )
    return occurrence


def set_cancellation_policy(
    actor: User, organization_id: int, market_id: int, *, vendor_cutoff_hours: int | None
) -> Market:
    """Set how long before a date starts vendors may cancel for a refund
    (None: vendors can't cancel on their own). Applies to holds taken from
    now on; existing holds and bookings keep the terms they were offered."""
    if vendor_cutoff_hours is not None and not (
        0 <= vendor_cutoff_hours <= MAX_CANCELLATION_CUTOFF_HOURS
    ):
        raise InvalidRequest(
            f"The cutoff must be 0 to {MAX_CANCELLATION_CUTOFF_HOURS} hours.",
            code="cancellation_cutoff_invalid",
        )
    with transaction.atomic():
        _manager(actor, organization_id)
        market = _market_in(organization_id, market_id, lock=True)
        _require_not_archived(market)
        market.vendor_cancellation_cutoff_hours = vendor_cutoff_hours
        market.save(update_fields=["vendor_cancellation_cutoff_hours", "updated_at"])
    return market


# --- Recurrence series ------------------------------------------------------------


@dataclass(frozen=True)
class Generation:
    series: RecurrenceSeries
    created: int
    already_existed: bool


def create_series(
    actor: User, organization_id: int, market_id: int, *, rule: recurrence.WeeklyRule
) -> Generation:
    """Create a weekly series and its occurrences in one transaction.

    Repeating an identical request returns the existing series and creates
    nothing (occurrences that were moved or cancelled keep their state).
    If any generated start clashes with an existing occurrence that isn't
    this series' own slot, nothing is created (409 occurrence_conflict).
    """
    rule = recurrence.WeeklyRule(**{**rule.__dict__, "weekdays": tuple(sorted(set(rule.weekdays)))})
    with transaction.atomic():
        _manager(actor, organization_id)
        market = _market_in(organization_id, market_id, lock=True)
        _require_not_archived(market)
        zone = zone_for(market)
        recurrence.validate_rule(rule, today=timezone.now().astimezone(zone).date())
        slots = recurrence.build_slots(rule, zone)

        definition = {
            "interval_weeks": rule.interval_weeks,
            "weekdays": list(rule.weekdays),
            "start_date": rule.start_date,
            "end_date": rule.end_date,
            "local_start_time": rule.local_start_time,
            "local_end_time": rule.local_end_time,
        }
        series = RecurrenceSeries.objects.filter(market=market, **definition).first()
        existed = series is not None
        if series is None:
            series = RecurrenceSeries.objects.create(
                market=market, timezone=market.timezone, created_by=actor, **definition
            )

        present_slots = set(series.occurrences.values_list("series_slot_start", flat=True))
        missing = [s for s in slots if s.starts_at not in present_slots]
        clashes = EventOccurrence.objects.filter(
            market=market, starts_at__in=[s.starts_at for s in missing]
        )
        if clashes.exists():
            raise Conflict(
                "Some dates in this series clash with existing event dates. Nothing was created.",
                code="occurrence_conflict",
                details=[
                    {"occurrence_id": o.pk, "starts_at": o.starts_at.isoformat()}
                    for o in clashes.order_by("starts_at")
                ],
            )
        EventOccurrence.objects.bulk_create(
            EventOccurrence(
                market=market,
                starts_at=s.starts_at,
                ends_at=s.ends_at,
                series=series,
                series_slot_start=s.starts_at,
            )
            for s in missing
        )
    return Generation(series, created=len(missing), already_existed=existed)


def get_series(actor: User, organization_id: int, market_id: int, series_id: int):
    membership_for(actor, organization_id)
    market = _market_in(organization_id, market_id)
    series = market.series.filter(pk=series_id).first()
    if series is None:
        raise NotFound("Series not found.")
    return series
