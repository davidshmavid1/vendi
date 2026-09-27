from datetime import datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from markets import discovery
from markets.models import EventOccurrence, Market, MarketStatus
from organizations import services as org_services

URL = "/api/v1/public/markets"
CHICAGO = ZoneInfo("America/Chicago")


def _day(days, hour=8, zone=CHICAGO):
    local = timezone.now().astimezone(zone).date() + timedelta(days=days)
    return datetime.combine(local, time(hour), tzinfo=zone)


@pytest.fixture
def world(make_user, client):
    owner = make_user("owner@example.com")
    org = org_services.create_organization(owner, name="Riverside Org").organization

    def market(
        name,
        *,
        lat=None,
        lng=None,
        kind="FARMERS_MARKET",
        days=(3,),
        status="PUBLISHED",
        tz="America/Chicago",
        city="Springfield",
        region="IL",
        venue="Park",
    ):
        m = Market.objects.create(
            organization=org,
            name=name,
            market_type=kind,
            venue_name=venue,
            address_line1="1 Main St",
            city=city,
            region=region,
            country="US",
            latitude=None if lat is None else Decimal(str(lat)),
            longitude=None if lng is None else Decimal(str(lng)),
            timezone=tz,
            status=status,
        )
        zone = ZoneInfo(tz)
        for d in days:
            EventOccurrence.objects.create(
                market=m, starts_at=_day(d, 8, zone), ends_at=_day(d, 13, zone)
            )
        return m

    return SimpleNamespace(org=org, owner=owner, market=market)


def _get(client, **params):
    return client.get(URL, params)


def _names(response):
    return [i["name"] for i in response.json()["items"]]


# --- Eligibility -------------------------------------------------------------------------


@pytest.mark.django_db
def test_only_published_markets_with_upcoming_scheduled_dates(world, client):
    world.market("Live", days=(2,))
    world.market("Draft", status="DRAFT")
    world.market("Archived", status="ARCHIVED")
    world.market("Only past", days=(-3,))
    only_cancelled = world.market("Only cancelled", days=(4,))
    only_cancelled.occurrences.update(status="CANCELLED", cancelled_at=timezone.now())
    world.market("No dates", days=())

    assert _names(_get(client)) == ["Live"]


@pytest.mark.django_db
def test_one_result_per_market_with_next_date_and_bounded_preview(world, client):
    world.market("Busy", days=(9, 2, 5, 7, 11))

    items = _get(client).json()["items"]

    assert len(items) == 1
    item = items[0]
    assert item["next_occurrence"]["local_date"] == _day(2).date().isoformat()
    assert [o["local_date"] for o in item["upcoming_preview"]] == [
        _day(d).date().isoformat() for d in (2, 5, 7)
    ]
    assert item["next_occurrence"]["local_start_time"] == "08:00:00"
    assert item["next_occurrence"]["timezone"] == "America/Chicago"


@pytest.mark.django_db
def test_cancelled_next_date_is_skipped_for_the_following_one(world, client):
    market = world.market("M", days=(2, 4))
    market.occurrences.filter(starts_at=_day(2)).update(
        status="CANCELLED", cancelled_at=timezone.now()
    )

    item = _get(client).json()["items"][0]

    assert item["next_occurrence"]["local_date"] == _day(4).date().isoformat()


# --- Filters -------------------------------------------------------------------------------


@pytest.mark.django_db
def test_text_type_and_date_filters_combine(world, client):
    world.market("Riverside Farmers", days=(2,), venue="Riverside Park")
    world.market("Night Popup", kind="POPUP", days=(10,), city="Chicago", region="IL")
    world.market("Harbor Market", days=(20,), city="Seattle", region="WA")

    assert _names(_get(client, q="riverside")) == ["Riverside Farmers"]
    assert _names(_get(client, q="chicago")) == ["Night Popup"]
    assert _names(_get(client, q="wa")) == ["Harbor Market"]
    assert _names(_get(client, market_type="POPUP")) == ["Night Popup"]
    in_range = {"date_from": _day(5).date(), "date_to": _day(15).date()}
    assert _names(_get(client, **in_range)) == ["Night Popup"]
    assert _names(_get(client, market_type="FARMERS_MARKET", **in_range)) == []
    assert _names(_get(client, q="market", date_from=_day(15).date())) == ["Harbor Market"]


@pytest.mark.django_db
def test_date_range_uses_each_markets_local_calendar_date(world, client):
    # 23:30 local in Los Angeles is already the next day in UTC.
    la = ZoneInfo("America/Los_Angeles")
    market = world.market("Late", tz="America/Los_Angeles", days=())
    local_day = timezone.now().astimezone(la).date() + timedelta(days=3)
    EventOccurrence.objects.create(
        market=market,
        starts_at=datetime.combine(local_day, time(23, 30), tzinfo=la),
        ends_at=datetime.combine(local_day, time(23, 59), tzinfo=la),
    )

    assert _names(_get(client, date_from=local_day, date_to=local_day)) == ["Late"]
    next_day = local_day + timedelta(days=1)
    assert _names(_get(client, date_from=next_day, date_to=next_day)) == []


@pytest.mark.django_db
def test_preview_respects_the_date_filter(world, client):
    world.market("M", days=(2, 8, 9, 30))

    item = _get(client, date_from=_day(5).date(), date_to=_day(10).date()).json()["items"][0]

    assert [o["local_date"] for o in item["upcoming_preview"]] == [
        _day(8).date().isoformat(),
        _day(9).date().isoformat(),
    ]


# --- Geography -------------------------------------------------------------------------------


@pytest.mark.django_db
def test_bounding_box_and_missing_coordinates(world, client):
    world.market("Springfield", lat=39.78, lng=-89.65)
    world.market("Seattle", lat=47.6, lng=-122.3)
    world.market("No coords")
    box = {"south": 35, "west": -95, "north": 45, "east": -85}

    assert _names(_get(client, **box)) == ["Springfield"]
    assert set(_names(_get(client))) == {"Springfield", "Seattle", "No coords"}
    map_items = client.get(f"{URL}/map", {"south": -90, "west": -180, "north": 90, "east": 180})
    assert {i["name"] for i in map_items.json()["items"]} == {"Springfield", "Seattle"}


@pytest.mark.django_db
def test_box_crossing_the_antimeridian(world, client):
    world.market("Fiji", lat=-17.7, lng=178.1)
    world.market("Samoa", lat=-13.8, lng=-172.1)
    world.market("Perth", lat=-31.9, lng=115.8)

    wrapped = {"south": -40, "west": 170, "north": 0, "east": -170}

    assert set(_names(_get(client, **wrapped))) == {"Fiji", "Samoa"}


@pytest.mark.django_db
def test_nearby_search_orders_by_distance_in_km(world, client):
    world.market("Close", lat=39.7817, lng=-89.6501, days=(9,))
    world.market("Nearish", lat=39.95, lng=-89.65, days=(2,))
    world.market("Far", lat=41.88, lng=-87.62, days=(2,))

    items = _get(client, lat=39.78, lng=-89.65, radius_km=30).json()["items"]

    assert [i["name"] for i in items] == ["Close", "Nearish"]
    assert items[0]["distance_km"] < 1
    assert 18 < items[1]["distance_km"] < 20


@pytest.mark.django_db
def test_nearby_search_across_the_antimeridian_and_near_a_pole(world, client):
    world.market("East of line", lat=-17.0, lng=179.9)
    world.market("Polar station", lat=89.5, lng=100.0)

    across = _names(_get(client, lat=-17.0, lng=-179.9, radius_km=50))
    polar = _names(_get(client, lat=89.9, lng=-80.0, radius_km=100))

    assert across == ["East of line"]
    assert polar == ["Polar station"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"lat": 91, "lng": 0, "radius_km": 5}, "coordinates_invalid"),
        ({"lat": 10, "lng": 181, "radius_km": 5}, "coordinates_invalid"),
        ({"lat": 10, "lng": 10}, "near_invalid"),
        ({"lat": 10, "lng": 10, "radius_km": 0}, "radius_invalid"),
        ({"lat": 10, "lng": 10, "radius_km": 501}, "radius_invalid"),
        ({"south": 10, "west": 0, "north": 5, "east": 5}, "bbox_invalid"),
        ({"south": 10, "west": 0, "north": 20}, "bbox_invalid"),
        ({"south": -91, "west": 0, "north": 5, "east": 5}, "coordinates_invalid"),
        ({"date_from": "2026-10-10", "date_to": "2026-10-01"}, "date_range_invalid"),
        ({"date_from": "2026-01-01", "date_to": "2027-06-01"}, "date_range_invalid"),
        ({"q": "x" * 101}, "query_invalid"),
        ({"market_type": "CARNIVAL"}, "validation_error"),
        ({"lat": "nan", "lng": 0, "radius_km": 5}, "coordinates_invalid"),
    ],
)
def test_input_validation(world, client, params, code):
    response = _get(client, **params)

    assert response.status_code in (400, 422)
    assert response.json()["error"]["code"] == code


# --- Ordering, pagination, map cap ---------------------------------------------------------------


@pytest.mark.django_db
def test_stable_ordering_and_offset_pagination(world, client):
    for i in range(5):
        world.market(f"Same day {i}", days=(3,))
    world.market("Earlier", days=(1,))

    first = _get(client, limit=4).json()
    second = _get(client, limit=4, cursor=first["next_cursor"]).json()

    names = [i["name"] for i in first["items"] + second["items"]]
    assert names == ["Earlier"] + [f"Same day {i}" for i in range(5)]  # ties by id
    assert first["next_cursor"] == 4 and second["next_cursor"] is None


@pytest.mark.django_db
def test_map_requires_an_area_and_reports_truncation(world, client):
    for i in range(4):
        world.market(f"M{i}", lat=40 + i / 100, lng=-89)

    missing = client.get(f"{URL}/map")
    with mock.patch.object(discovery, "MAP_MAX_MARKERS", 3):
        capped = client.get(f"{URL}/map", {"south": 39, "west": -90, "north": 41, "east": -88})

    assert missing.json()["error"]["code"] == "bbox_invalid"
    body = capped.json()
    assert (len(body["items"]), body["total"], body["truncated"], body["limit"]) == (3, 4, True, 3)


@pytest.mark.django_db
def test_list_and_map_share_eligibility(world, client):
    world.market("Popup", kind="POPUP", lat=40, lng=-89)
    world.market("Farm", lat=40.01, lng=-89)
    area = {"south": 39, "west": -90, "north": 41, "east": -88, "market_type": "POPUP"}

    listed = _names(_get(client, **area))
    mapped = [i["name"] for i in client.get(f"{URL}/map", area).json()["items"]]

    assert listed == mapped == ["Popup"]


# --- Privacy -----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_public_results_expose_no_private_data(world, client):
    world.market("M", lat=40, lng=-89)

    for response in (
        _get(client),
        client.get(f"{URL}/map", {"south": 39, "west": -90, "north": 41, "east": -88}),
    ):
        text = response.content.decode()
        item = response.json()["items"][0]
        assert "owner@example.com" not in text and "Riverside Org" not in text
        assert not {"organization", "organization_id", "status", "address_line1"} & set(item)


@pytest.mark.django_db
def test_published_markets_status_is_required_even_with_dates(world, client):
    market = world.market("M")
    Market.objects.filter(pk=market.pk).update(status=MarketStatus.DRAFT)

    assert _get(client).json()["items"] == []
