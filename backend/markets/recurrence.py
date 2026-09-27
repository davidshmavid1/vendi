"""Weekly recurrence: turn a series definition into concrete local dates and
aware start/end instants. Pure functions, no database access.

Daylight-saving policy: a local start or end time that does not exist on a
date (spring-forward gap) or occurs twice (fall-back overlap) is rejected
for the whole request, listing the dates. Nothing is shifted silently.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings

from core.exceptions import InvalidRequest

WEEKDAY_NAMES = {1: "MO", 2: "TU", 3: "WE", 4: "TH", 5: "FR", 6: "SA", 7: "SU"}


@dataclass(frozen=True)
class WeeklyRule:
    interval_weeks: int
    weekdays: tuple[int, ...]  # ISO 1..7, sorted, unique
    start_date: date
    end_date: date
    local_start_time: time
    local_end_time: time


@dataclass(frozen=True)
class Slot:
    local_date: date
    starts_at: datetime
    ends_at: datetime


def validate_rule(rule: WeeklyRule, *, today: date) -> None:
    if not 1 <= rule.interval_weeks <= 12:
        raise InvalidRequest("interval_weeks must be between 1 and 12.", code="recurrence_invalid")
    if not rule.weekdays or any(d not in WEEKDAY_NAMES for d in rule.weekdays):
        raise InvalidRequest(
            "weekdays must list ISO weekdays 1 (Monday) to 7 (Sunday).",
            code="recurrence_invalid",
        )
    if rule.end_date < rule.start_date:
        raise InvalidRequest("end_date must not be before start_date.", code="recurrence_invalid")
    if rule.start_date < today:
        raise InvalidRequest("start_date is in the past.", code="recurrence_invalid")
    if (rule.end_date - rule.start_date).days >= settings.MARKET_SERIES_MAX_DAYS:
        raise InvalidRequest(
            f"A series may span at most {settings.MARKET_SERIES_MAX_DAYS} days.",
            code="recurrence_too_long",
        )
    if rule.local_end_time <= rule.local_start_time:
        raise InvalidRequest(
            "local_end_time must be after local_start_time; overnight events are not "
            "supported in a series. Create them individually.",
            code="recurrence_invalid",
        )


def local_dates(rule: WeeklyRule) -> list[date]:
    """Dates in [start_date, end_date] on the given weekdays, in weeks
    0, N, 2N, ... counted from the week (Monday-based) containing start_date."""
    first_monday = rule.start_date - timedelta(days=rule.start_date.isoweekday() - 1)
    dates = []
    day = rule.start_date
    while day <= rule.end_date:
        week_index = (day - first_monday).days // 7
        if week_index % rule.interval_weeks == 0 and day.isoweekday() in rule.weekdays:
            dates.append(day)
        day += timedelta(days=1)
    return dates


def to_instant(local_date: date, local_time: time, zone: ZoneInfo) -> datetime | None:
    """Aware datetime for a wall-clock time, or None when that time is
    ambiguous or does not exist in ``zone`` on that date."""
    early = datetime.combine(local_date, local_time, tzinfo=zone).replace(fold=0)
    late = early.replace(fold=1)
    if early.utcoffset() != late.utcoffset():
        return None  # ambiguous (repeated hour) or nonexistent (skipped hour)
    return early


def build_slots(rule: WeeklyRule, zone: ZoneInfo) -> list[Slot]:
    dates = local_dates(rule)
    if not dates:
        raise InvalidRequest(
            "This schedule produces no dates in the given range.", code="recurrence_empty"
        )
    if len(dates) > settings.MARKET_SERIES_MAX_OCCURRENCES:
        raise InvalidRequest(
            f"This schedule produces {len(dates)} dates; the limit is "
            f"{settings.MARKET_SERIES_MAX_OCCURRENCES} per request.",
            code="recurrence_too_many",
        )
    slots, unclear = [], []
    for day in dates:
        start = to_instant(day, rule.local_start_time, zone)
        end = to_instant(day, rule.local_end_time, zone)
        if start is None or end is None:
            unclear.append(day.isoformat())
            continue
        slots.append(Slot(day, start, end))
    if unclear:
        raise InvalidRequest(
            "These dates fall on a daylight-saving change where the local start or end "
            "time is skipped or repeated. Choose different times.",
            code="recurrence_dst_conflict",
            details=[{"date": d} for d in unclear],
        )
    return slots
