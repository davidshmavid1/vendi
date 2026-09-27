from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from markets import recurrence, services
from markets.models import EventOccurrence, Market, MarketStatus, OccurrenceStatus
from moderation import services as moderation_services
from organizations import services as org_services
from organizations.models import OrganizationMembership, Role

CHICAGO = ZoneInfo("America/Chicago")
VENUE = {
    "venue_name": "Riverside Park",
    "address_line1": "100 River Rd",
    "city": "Springfield",
    "region": "IL",
    "postal_code": "62701",
    "country": "us",
}


def _future(days, hour=8):
    day = timezone.now().astimezone(CHICAGO).date() + timedelta(days=days)
    return datetime.combine(day, time(hour), tzinfo=CHICAGO)


@pytest.fixture
def org(make_user, as_user):
    owner, admin = make_user("owner@example.com"), make_user("admin@example.com")
    staff, outsider = make_user("staff@example.com"), make_user("outsider@example.com")
    organization = org_services.create_organization(owner, name="Riverside").organization
    other = org_services.create_organization(outsider, name="Hilltop").organization
    OrganizationMembership.objects.create(organization=organization, user=admin, role=Role.ADMIN)
    OrganizationMembership.objects.create(organization=organization, user=staff, role=Role.STAFF)
    return SimpleNamespace(
        org=organization,
        other=other,
        owner=owner,
        outsider=outsider,
        client={
            "OWNER": as_user(owner),
            "ADMIN": as_user(admin),
            "STAFF": as_user(staff),
            "OUTSIDER": as_user(outsider),
        },
        url=f"/organizations/{organization.pk}/markets",
    )


def _market(org, **extra):
    return services.create_market(
        org.owner,
        org.org.pk,
        name="Saturday Market",
        market_type="FARMERS_MARKET",
        timezone="America/Chicago",
        **{**VENUE, **extra},
    )


def _published(org, **extra):
    market = _market(org, **extra)
    services.create_occurrence(
        org.owner, org.org.pk, market.pk, starts_at=_future(3), ends_at=_future(3, 12)
    )
    return services.publish_market(org.owner, org.org.pk, market.pk)


# --- Markets: permissions, validation, lifecycle --------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("role", "create", "read"),
    [("OWNER", 201, 200), ("ADMIN", 201, 200), ("STAFF", 403, 200), ("OUTSIDER", 404, 404)],
)
def test_market_permissions(org, role, create, read):
    existing = _market(org)
    client = org.client[role]

    created = client.post(
        org.url, {"name": "Popup", "market_type": "POPUP", "timezone": "America/Chicago"}
    )
    fetched = client.get(f"{org.url}/{existing.pk}")
    edited = client.patch(f"{org.url}/{existing.pk}", {"name": "Renamed"})

    assert created.status_code == create
    assert fetched.status_code == read
    assert edited.status_code == (200 if create == 201 else create)


@pytest.mark.django_db
def test_market_ids_from_another_organization_are_not_found(org):
    foreign = Market.objects.create(
        organization=org.other, name="Theirs", market_type="POPUP", timezone="UTC"
    )
    owner = org.client["OWNER"]

    assert owner.get(f"{org.url}/{foreign.pk}").status_code == 404
    assert owner.patch(f"{org.url}/{foreign.pk}", {"name": "x"}).status_code == 404
    assert owner.post(f"{org.url}/{foreign.pk}/publish").status_code == 404
    assert owner.get(f"/organizations/{org.other.pk}/markets").status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"timezone": "Mars/Olympus"}, "timezone_invalid"),
        ({"country": "USA"}, "validation_error"),
        ({"country": "1A"}, "country_invalid"),
        ({"latitude": "41.8"}, "coordinates_invalid"),
        ({"latitude": "91", "longitude": "0"}, "coordinates_invalid"),
        ({"latitude": "0", "longitude": "-181"}, "coordinates_invalid"),
        ({"organization_id": 1}, "validation_error"),
        ({"status": "PUBLISHED"}, "validation_error"),
    ],
)
def test_market_validation(org, body, code):
    base = {"name": "M", "market_type": "POPUP", "timezone": "America/Chicago"}

    response = org.client["OWNER"].post(org.url, {**base, **body})

    assert response.json()["error"]["code"] == code
    assert not Market.objects.exists()


@pytest.mark.django_db
def test_coordinates_and_country_are_normalized(org):
    response = org.client["OWNER"].post(
        org.url,
        {
            "name": "M",
            "market_type": "POPUP",
            "timezone": "America/Chicago",
            "country": "us",
            "latitude": "39.7817",
            "longitude": "-89.6501",
        },
    )

    assert response.status_code == 201
    market = Market.objects.get()
    assert market.country == "US" and market.has_coordinates


@pytest.mark.django_db
def test_publication_requires_venue_and_an_upcoming_date(org):
    bare = services.create_market(
        org.owner, org.org.pk, name="Bare", market_type="POPUP", timezone="UTC"
    )

    response = org.client["OWNER"].post(f"{org.url}/{bare.pk}/publish")

    assert response.status_code == 400
    missing = {d["field"] for d in response.json()["error"]["details"]}
    assert missing == {"venue_name", "address_line1", "city", "country", "occurrences"}

    market = _market(org)
    past = EventOccurrence.objects.create(
        market=market,
        starts_at=timezone.now() - timedelta(days=2),
        ends_at=timezone.now() - timedelta(days=2) + timedelta(hours=4),
    )
    assert org.client["OWNER"].post(f"{org.url}/{market.pk}/publish").status_code == 400
    services.create_occurrence(
        org.owner, org.org.pk, market.pk, starts_at=_future(3), ends_at=_future(3, 12)
    )
    published = org.client["ADMIN"].post(f"{org.url}/{market.pk}/publish")
    assert published.status_code == 200
    assert published.json()["status"] == "PUBLISHED"
    assert past.pk  # past dates are kept


@pytest.mark.django_db
def test_published_market_keeps_its_venue(org):
    market = _published(org)

    response = org.client["OWNER"].patch(f"{org.url}/{market.pk}", {"city": ""})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "publication_requirements"
    with pytest.raises(IntegrityError), transaction.atomic():
        Market.objects.filter(pk=market.pk).update(venue_name="")


@pytest.mark.django_db
def test_archive_is_terminal_and_keeps_records(org):
    market = _published(org)

    archived = org.client["OWNER"].post(f"{org.url}/{market.pk}/archive")
    again = org.client["OWNER"].post(f"{org.url}/{market.pk}/archive")
    edit = org.client["OWNER"].patch(f"{org.url}/{market.pk}", {"name": "x"})
    publish = org.client["OWNER"].post(f"{org.url}/{market.pk}/publish")

    assert archived.json()["status"] == "ARCHIVED"
    assert {again.status_code, edit.status_code, publish.status_code} == {409}
    assert market.occurrences.count() == 1


@pytest.mark.django_db
def test_timezone_is_locked_once_dates_exist(org):
    market = _published(org)

    response = org.client["OWNER"].patch(f"{org.url}/{market.pk}", {"timezone": "UTC"})

    assert response.json()["error"]["code"] == "timezone_locked"


# --- Occurrences ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_create_edit_and_cancel_occurrence(org):
    market = _market(org)
    base = f"{org.url}/{market.pk}/occurrences"
    created = org.client["OWNER"].post(
        base,
        {"starts_at": _future(5).isoformat(), "ends_at": _future(5, 13).isoformat()},
    )
    occurrence_id = created.json()["id"]

    assert created.status_code == 201
    assert created.json()["local_start_time"] == "08:00:00"
    assert created.json()["timezone"] == "America/Chicago"
    moved = org.client["ADMIN"].patch(
        f"{base}/{occurrence_id}", {"ends_at": _future(5, 14).isoformat()}
    )
    assert moved.json()["local_end_time"] == "14:00:00"
    staff_cancel = org.client["STAFF"].post(f"{base}/{occurrence_id}/cancel")
    assert staff_cancel.status_code == 403

    cancelled = org.client["OWNER"].post(
        f"{base}/{occurrence_id}/cancel", {"message": "Flooding at the park."}
    )
    again = org.client["OWNER"].post(f"{base}/{occurrence_id}/cancel")
    edit_cancelled = org.client["OWNER"].patch(
        f"{base}/{occurrence_id}", {"ends_at": _future(5, 15).isoformat()}
    )

    assert cancelled.json()["status"] == "CANCELLED"
    assert again.json()["error"]["code"] == "occurrence_cancelled"
    assert edit_cancelled.status_code == 409
    assert EventOccurrence.objects.filter(pk=occurrence_id).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("starts", "ends", "code"),
    [
        ("2026-10-03T08:00:00", "2026-10-03T12:00:00", "timestamp_invalid"),
        ("2026-10-03T12:00:00-05:00", "2026-10-03T08:00:00-05:00", "timestamp_invalid"),
        ("2026-10-03T08:00:00-05:00", "2026-10-03T08:00:00-05:00", "timestamp_invalid"),
    ],
)
def test_occurrence_timestamp_validation(org, starts, ends, code):
    market = _market(org)

    response = org.client["OWNER"].post(
        f"{org.url}/{market.pk}/occurrences", {"starts_at": starts, "ends_at": ends}
    )

    assert response.json()["error"]["code"] == code


@pytest.mark.django_db
def test_duplicate_start_is_rejected_by_api_and_database(org):
    market = _market(org)
    body = {"starts_at": _future(5).isoformat(), "ends_at": _future(5, 12).isoformat()}
    org.client["OWNER"].post(f"{org.url}/{market.pk}/occurrences", body)

    duplicate = org.client["OWNER"].post(f"{org.url}/{market.pk}/occurrences", body)

    assert duplicate.json()["error"]["code"] == "occurrence_exists"
    with pytest.raises(IntegrityError), transaction.atomic():
        EventOccurrence.objects.create(market=market, starts_at=_future(5), ends_at=_future(6))
    with pytest.raises(IntegrityError), transaction.atomic():
        EventOccurrence.objects.create(market=market, starts_at=_future(7), ends_at=_future(7))


@pytest.mark.django_db
def test_overlapping_occurrences_are_allowed(org):
    market = _market(org)
    services.create_occurrence(
        org.owner, org.org.pk, market.pk, starts_at=_future(5, 8), ends_at=_future(5, 14)
    )

    services.create_occurrence(
        org.owner, org.org.pk, market.pk, starts_at=_future(5, 10), ends_at=_future(5, 16)
    )

    assert market.occurrences.count() == 2


# --- Recurrence ----------------------------------------------------------------------------


def _series_body(start_offset=7, days=27, **extra):
    today = timezone.now().astimezone(CHICAGO).date()
    body = {
        "interval_weeks": 1,
        "weekdays": [3, 6],
        "start_date": (today + timedelta(days=start_offset)).isoformat(),
        "end_date": (today + timedelta(days=start_offset + days)).isoformat(),
        "local_start_time": "08:00",
        "local_end_time": "12:30",
    }
    return {**body, **extra}


def test_weekly_dates_respect_weekdays_interval_and_inclusive_bounds():
    rule = recurrence.WeeklyRule(
        interval_weeks=2,
        weekdays=(2, 6),
        start_date=date(2026, 9, 29),  # Tuesday
        end_date=date(2026, 10, 27),  # Tuesday
        local_start_time=time(8),
        local_end_time=time(12),
    )

    assert recurrence.local_dates(rule) == [
        date(2026, 9, 29),
        date(2026, 10, 3),
        date(2026, 10, 13),
        date(2026, 10, 17),
        date(2026, 10, 27),
    ]


def test_wall_clock_time_is_kept_across_daylight_saving():
    rule = recurrence.WeeklyRule(
        interval_weeks=1,
        weekdays=(7,),
        start_date=date(2026, 10, 25),
        end_date=date(2026, 11, 8),
        local_start_time=time(8),
        local_end_time=time(12),
    )

    slots = recurrence.build_slots(rule, CHICAGO)

    assert [s.starts_at.astimezone(CHICAGO).hour for s in slots] == [8, 8, 8]
    assert [s.starts_at.utcoffset().total_seconds() / 3600 for s in slots] == [-5, -6, -6]


@pytest.mark.parametrize(
    ("day", "start"),
    [(date(2027, 3, 14), time(2, 30)), (date(2026, 11, 1), time(1, 30))],
    ids=["nonexistent", "ambiguous"],
)
def test_skipped_or_repeated_local_times_are_rejected(day, start):
    rule = recurrence.WeeklyRule(1, (7,), day, day, start, time(10))

    with pytest.raises(recurrence.InvalidRequest) as excinfo:
        recurrence.build_slots(rule, CHICAGO)

    assert excinfo.value.code == "recurrence_dst_conflict"
    assert excinfo.value.details == [{"date": day.isoformat()}]


@pytest.mark.django_db
def test_series_generates_occurrences_and_is_idempotent(org):
    market = _market(org)
    url = f"{org.url}/{market.pk}/series"

    first = org.client["ADMIN"].post(url, _series_body())
    series_id = first.json()["series"]["id"]
    generated = list(EventOccurrence.objects.filter(series_id=series_id).order_by("starts_at"))
    services.cancel_occurrence(org.owner, org.org.pk, market.pk, generated[0].pk, message="Rain")
    services.update_occurrence(
        org.owner,
        org.org.pk,
        market.pk,
        generated[1].pk,
        starts_at=generated[1].starts_at + timedelta(hours=1),
    )
    second = org.client["ADMIN"].post(url, _series_body())

    assert first.status_code == 201
    assert first.json()["created_occurrences"] == len(generated) == 8
    assert {o.starts_at.astimezone(CHICAGO).isoweekday() for o in generated} == {3, 6}
    assert second.status_code == 200
    assert second.json() == {**second.json(), "created_occurrences": 0, "already_existed": True}
    assert market.occurrences.count() == 8
    generated[0].refresh_from_db()
    generated[1].refresh_from_db()
    assert generated[0].status == OccurrenceStatus.CANCELLED
    assert generated[1].starts_at.astimezone(CHICAGO).hour == 9
    detail = org.client["STAFF"].get(f"{url}/{series_id}").json()
    assert detail["occurrence_count"] == 8 and detail["weekdays"] == [3, 6]
    listed = (
        org.client["STAFF"]
        .get(f"{org.url}/{market.pk}/occurrences?series_id={series_id}&limit=5")
        .json()
    )
    assert len(listed["items"]) == 5 and listed["next_cursor"]


@pytest.mark.django_db
def test_series_conflicting_with_independent_date_creates_nothing(org):
    market = _market(org)
    body = _series_body()
    first_day = date.fromisoformat(body["start_date"])
    while first_day.isoweekday() not in (3, 6):
        first_day += timedelta(days=1)
    clash = services.create_occurrence(
        org.owner,
        org.org.pk,
        market.pk,
        starts_at=datetime.combine(first_day, time(8), tzinfo=CHICAGO),
        ends_at=datetime.combine(first_day, time(11), tzinfo=CHICAGO),
    )

    response = org.client["OWNER"].post(f"{org.url}/{market.pk}/series", body)

    assert response.status_code == 409
    assert response.json()["error"]["details"][0]["occurrence_id"] == clash.pk
    assert market.occurrences.count() == 1
    assert not market.series.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"days": 400}, "recurrence_too_long"),
        ({"start_offset": -3}, "recurrence_invalid"),
        ({"weekdays": [1, 2, 3, 4, 5, 6, 7], "days": 360}, "recurrence_too_many"),
        ({"weekdays": [8]}, "recurrence_invalid"),
        ({"local_end_time": "07:00"}, "recurrence_invalid"),
        ({"interval_weeks": 13}, "validation_error"),
        ({"frequency": "DAILY"}, "validation_error"),
        ({"by_month_day": 1}, "validation_error"),
    ],
)
def test_series_limits_and_unsupported_patterns(org, changes, code):
    market = _market(org)
    body = _series_body(
        start_offset=changes.pop("start_offset", 7), days=changes.pop("days", 27), **changes
    )

    response = org.client["OWNER"].post(f"{org.url}/{market.pk}/series", body)

    assert response.json()["error"]["code"] == code
    assert not market.occurrences.exists()


@pytest.mark.django_db
def test_series_empty_range_is_rejected(org):
    market = _market(org)
    today = timezone.now().astimezone(CHICAGO).date()
    day = today + timedelta(days=7)
    while day.isoweekday() != 1:
        day += timedelta(days=1)

    response = org.client["OWNER"].post(
        f"{org.url}/{market.pk}/series",
        _series_body(start_offset=(day - today).days, days=0, weekdays=[3]),
    )

    assert response.json()["error"]["code"] == "recurrence_empty"


# --- Public endpoints -----------------------------------------------------------------------


@pytest.mark.django_db
def test_public_market_shows_only_published_and_public_fields(org, api):
    draft = _market(org)
    published = _published(org, latitude="39.78", longitude="-89.65")

    response = api.get(f"/public/markets/{published.pk}")

    assert api.get(f"/public/markets/{draft.pk}").status_code == 404
    assert response.status_code == 200
    body = response.json()
    assert body["organizer"] == {"name": "Riverside"}
    assert "organization" not in body and "status" not in body
    assert "owner@example.com" not in response.content.decode()


@pytest.mark.django_db
def test_public_occurrences_show_cancelled_dates_and_hide_past_ones(org, api):
    market = _published(org)
    extra = services.create_occurrence(
        org.owner, org.org.pk, market.pk, starts_at=_future(10), ends_at=_future(10, 12)
    )
    services.cancel_occurrence(org.owner, org.org.pk, market.pk, extra.pk, message="Storm")
    EventOccurrence.objects.create(
        market=market,
        starts_at=timezone.now() - timedelta(days=3),
        ends_at=timezone.now() - timedelta(days=3) + timedelta(hours=2),
    )

    items = api.get(f"/public/markets/{market.pk}/occurrences").json()["items"]
    detail = api.get(f"/public/occurrences/{extra.pk}").json()

    assert [i["status"] for i in items] == ["SCHEDULED", "CANCELLED"]
    assert items[1]["cancellation_message"] == "Storm"
    assert "cancelled_at" not in items[0] and "series_id" not in items[0]
    assert detail["status"] == "CANCELLED" and detail["market"]["id"] == market.pk


@pytest.mark.django_db
def test_archived_market_disappears_publicly(org, api):
    market = _published(org)
    occurrence = market.occurrences.get()
    services.archive_market(org.owner, org.org.pk, market.pk)

    assert api.get(f"/public/markets/{market.pk}").status_code == 404
    assert api.get(f"/public/markets/{market.pk}/occurrences").status_code == 404
    assert api.get(f"/public/occurrences/{occurrence.pk}").status_code == 404


@pytest.mark.django_db
def test_restricted_account_can_still_browse(org, make_user, as_user):
    market = _published(org)
    visitor = make_user("visitor@example.com")
    moderation_services.create_restriction(
        org.owner, org.org.pk, account_id=visitor.pk, reason="No-shows"
    )

    assert as_user(visitor).get(f"/public/markets/{market.pk}").status_code == 200


# --- Auth and CSRF -------------------------------------------------------------------------


@pytest.mark.django_db
def test_organizer_endpoints_require_session_and_csrf(org, api):
    market = _market(org)
    owner = org.client["OWNER"]

    assert api.get(org.url).status_code == 401
    responses = [
        owner.post(org.url, {"name": "M", "market_type": "POPUP", "timezone": "UTC"}, csrf=False),
        owner.patch(f"{org.url}/{market.pk}", {"name": "x"}, csrf=False),
        owner.post(f"{org.url}/{market.pk}/publish", csrf=False),
        owner.post(f"{org.url}/{market.pk}/series", _series_body(), csrf=False),
    ]
    assert {r.json()["error"]["code"] for r in responses} == {"csrf_failed"}
    assert Market.objects.count() == 1


@pytest.mark.django_db
def test_publication_rechecks_time_at_publish(org):
    market = _market(org)
    services.create_occurrence(
        org.owner, org.org.pk, market.pk, starts_at=_future(1), ends_at=_future(1, 12)
    )
    later = timezone.now() + timedelta(days=5)

    with mock.patch("markets.models.timezone.now", return_value=later):
        with pytest.raises(services.InvalidRequest):
            services.publish_market(org.owner, org.org.pk, market.pk)
    assert Market.objects.get(pk=market.pk).status == MarketStatus.DRAFT
