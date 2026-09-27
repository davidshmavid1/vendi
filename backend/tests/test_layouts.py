from datetime import datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from layouts.geometry import Rect, overlapping_pairs
from layouts.models import Stall, StallLayout
from markets import services as market_services
from markets.models import EventOccurrence
from organizations import services as org_services
from organizations.models import OrganizationMembership, Role
from vendors import services as vendor_services

CHICAGO = ZoneInfo("America/Chicago")
VENUE = {"venue_name": "Park", "address_line1": "1 Main", "city": "Springfield", "country": "US"}


def _future(days, hour=8):
    day = timezone.now().astimezone(CHICAGO).date() + timedelta(days=days)
    return datetime.combine(day, time(hour), tzinfo=CHICAGO)


def stall(label="A1", x=0, y=0, width=10, height=10, price=2500, **extra):
    return {
        "label": label,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "price_minor": price,
        **extra,
    }


def body(stalls=None, revision=None, **extra):
    return {
        "expected_revision": revision,
        "canvas_width": 100,
        "canvas_height": 60,
        "currency": "USD",
        "stalls": stalls if stalls is not None else [stall()],
        **extra,
    }


@pytest.fixture
def world(make_user, as_user):
    owner, admin = make_user("owner@example.com"), make_user("admin@example.com")
    staff, outsider = make_user("staff@example.com"), make_user("outsider@example.com")
    vendor = make_user("vendor@example.com")
    org = org_services.create_organization(owner, name="Riverside").organization
    other_org = org_services.create_organization(outsider, name="Hilltop").organization
    OrganizationMembership.objects.create(organization=org, user=admin, role=Role.ADMIN)
    OrganizationMembership.objects.create(organization=org, user=staff, role=Role.STAFF)
    vendor_services.create_business(
        vendor, name="Bees", category="PRODUCE", contact_email="b@x.example"
    )
    market = market_services.create_market(
        owner, org.pk, name="Saturday", market_type="POPUP", timezone="America/Chicago", **VENUE
    )
    first = market_services.create_occurrence(
        owner, org.pk, market.pk, starts_at=_future(5), ends_at=_future(5, 12)
    )
    second = market_services.create_occurrence(
        owner, org.pk, market.pk, starts_at=_future(12), ends_at=_future(12, 12)
    )
    market_services.publish_market(owner, org.pk, market.pk)

    def url(occurrence=first, organization=org):
        return (
            f"/organizations/{organization.pk}/markets/{market.pk}"
            f"/occurrences/{occurrence.pk}/layout"
        )

    return SimpleNamespace(
        org=org,
        other_org=other_org,
        owner=owner,
        market=market,
        first=first,
        second=second,
        url=url,
        public=lambda occurrence=first: f"/public/occurrences/{occurrence.pk}/layout",
        client={
            "OWNER": as_user(owner),
            "ADMIN": as_user(admin),
            "STAFF": as_user(staff),
            "OUTSIDER": as_user(outsider),
            "VENDOR": as_user(vendor),
        },
    )


def _create(world, stalls=None, **extra):
    response = world.client["OWNER"].put(world.url(), body(stalls, **extra))
    assert response.status_code == 200, response.content
    return response.json()


def _problems(response):
    assert response.status_code == 400, response.content
    return response.json()["error"]


# --- Permissions and isolation ---------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("role", "write", "read"),
    [
        ("OWNER", 200, 200),
        ("ADMIN", 200, 200),
        ("STAFF", 403, 404),
        ("OUTSIDER", 404, 404),
        ("VENDOR", 404, 404),
    ],
)
def test_layout_role_permissions(world, role, write, read):
    # STAFF may read, but there's nothing yet: layout_not_found (404).
    response = world.client[role].put(world.url(), body())
    assert response.status_code == write
    if write != 200:
        _create(world)
        read = 200 if role == "STAFF" else read
    assert world.client[role].get(world.url()).status_code == read


@pytest.mark.django_db
def test_staff_cannot_publish_or_unpublish(world):
    layout = _create(world)
    staff = world.client["STAFF"]
    assert (
        staff.post(f"{world.url()}/publish", {"expected_revision": layout["revision"]}).status_code
        == 403
    )
    assert staff.post(f"{world.url()}/unpublish").status_code == 403


@pytest.mark.django_db
def test_other_organization_cannot_reach_the_occurrence(world):
    _create(world)
    foreign = world.url(organization=world.other_org)
    outsider = world.client["OUTSIDER"]
    assert outsider.get(foreign).status_code == 404
    assert outsider.put(foreign, body()).status_code == 404
    assert outsider.post(f"{foreign}/unpublish").status_code == 404


@pytest.mark.django_db
def test_occurrences_have_independent_layouts(world):
    first = _create(world, [stall("A1")])
    response = world.client["OWNER"].put(world.url(world.second), body([stall("A1", price=900)]))
    second = response.json()
    assert first["id"] != second["id"]
    assert first["stalls"][0]["id"] != second["stalls"][0]["id"]
    # Editing the second date leaves the first untouched.
    edited = [{**second["stalls"][0], "price_minor": 1}]
    world.client["OWNER"].put(world.url(world.second), body(_ins(edited), revision=1))
    assert world.client["OWNER"].get(world.url()).json()["stalls"][0]["price_minor"] == 2500


@pytest.mark.django_db
def test_stall_ids_from_another_layout_are_rejected(world):
    other = world.client["OWNER"].put(world.url(world.second), body([stall("B1")])).json()
    _create(world)
    mine = world.client["OWNER"].get(world.url()).json()
    stalls = [*_ins(mine["stalls"]), {**stall("B1", x=50), "id": other["stalls"][0]["id"]}]
    error = _problems(world.client["OWNER"].put(world.url(), body(stalls, revision=1)))
    assert error["code"] == "stall_mismatch"
    assert Stall.objects.get(pk=other["stalls"][0]["id"]).layout_id == other["id"]


@pytest.mark.django_db
def test_client_cannot_set_layout_internals(world):
    response = world.client["OWNER"].put(world.url(), body(occurrence_id=world.second.pk))
    assert response.status_code == 422
    response = world.client["OWNER"].put(world.url(), body(published_at="2030-01-01T00:00:00Z"))
    assert response.status_code == 422
    response = world.client["OWNER"].put(world.url(), body([{**stall(), "layout_id": 1}]))
    assert response.status_code == 422


@pytest.mark.django_db
def test_auth_and_csrf(api, world):
    assert api.put(world.url(), body()).status_code == 401
    response = world.client["OWNER"].put(world.url(), body(), csrf=False)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"
    assert not StallLayout.objects.exists()


# --- Revisions and atomic saves -----------------------------------------------------------


def _ins(stalls):
    """Turn response stalls back into request stalls (drop read-only fields)."""
    keep = (
        "id",
        "label",
        "description",
        "x",
        "y",
        "width",
        "height",
        "physical_width",
        "physical_depth",
        "physical_unit",
        "enabled",
        "price_minor",
    )
    return [{k: s[k] for k in keep} for s in stalls]


@pytest.mark.django_db
def test_revisions_and_stale_edits(world):
    created = _create(world)
    assert (created["revision"], created["published"]) == (1, False)
    stalls = _ins(created["stalls"])
    updated = world.client["ADMIN"].put(world.url(), body(stalls, revision=1)).json()
    assert updated["revision"] == 2
    stale = world.client["OWNER"].put(world.url(), body(stalls, revision=1))
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_revision"
    assert stale.json()["error"]["details"] == [{"current_revision": 2}]
    # Creating again when one exists is stale too.
    assert (
        world.client["OWNER"].put(world.url(), body()).json()["error"]["code"] == "stale_revision"
    )


@pytest.mark.django_db
def test_invalid_stall_leaves_layout_unchanged(world):
    created = _create(world, [stall("A1"), stall("A2", x=10)])
    stalls = _ins(created["stalls"])
    stalls[0]["price_minor"] = 9999
    stalls.append(stall("A3", x=95))  # sticks out of the 100-wide canvas
    error = _problems(world.client["OWNER"].put(world.url(), body(stalls, revision=1)))
    assert error["code"] == "layout_invalid"
    assert error["details"][0]["index"] == 2
    layout = StallLayout.objects.get()
    assert layout.revision == 1
    assert sorted(layout.stalls.values_list("label", "price_minor")) == [("A1", 2500), ("A2", 2500)]


@pytest.mark.django_db
def test_stable_ids_across_edits(world):
    created = _create(world, [stall("A1"), stall("A2", x=10)])
    ids = [s["id"] for s in created["stalls"]]
    stalls = _ins(created["stalls"])
    stalls[1] |= {"label": "A2-corner", "x": 40, "enabled": False}
    stalls.append(stall("A3", x=20))
    updated = world.client["OWNER"].put(world.url(), body(stalls, revision=1)).json()
    assert [s["id"] for s in updated["stalls"]][:2] == ids
    assert updated["stalls"][1]["label"] == "A2-corner"
    assert updated["stalls"][1]["enabled"] is False
    assert len(updated["stalls"]) == 3


@pytest.mark.django_db
def test_stalls_cannot_be_removed_by_omission(world):
    created = _create(world, [stall("A1"), stall("A2", x=10)])
    stalls = _ins(created["stalls"])[:1]
    error = _problems(world.client["OWNER"].put(world.url(), body(stalls, revision=1)))
    assert error["code"] == "stall_mismatch"
    assert Stall.objects.count() == 2


@pytest.mark.django_db
def test_duplicate_stall_id_in_payload(world):
    created = _create(world)
    stalls = _ins(created["stalls"]) * 2
    stalls[1]["label"] = "Other"
    assert _problems(world.client["OWNER"].put(world.url(), body(stalls, revision=1)))["code"] == (
        "stall_mismatch"
    )


@pytest.mark.django_db
def test_labels_can_be_swapped(world):
    created = _create(world, [stall("A1"), stall("A2", x=10)])
    stalls = _ins(created["stalls"])
    stalls[0]["label"], stalls[1]["label"] = "a2", "A1"
    response = world.client["OWNER"].put(world.url(), body(stalls, revision=1))
    assert [s["label"] for s in response.json()["stalls"]] == ["a2", "A1"]


# --- Labels -------------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("second", ["A1", "a1", " A1 "])
def test_labels_are_unique_case_insensitively(world, second):
    error = _problems(
        world.client["OWNER"].put(world.url(), body([stall("A1"), stall(second, x=20)]))
    )
    assert error["details"] == [
        {
            "index": 1,
            "stall_id": None,
            "field": "label",
            "message": "Another stall already uses this label.",
        }
    ]


@pytest.mark.django_db
def test_label_uniqueness_is_a_database_constraint(world):
    created = _create(world)
    layout = StallLayout.objects.get(pk=created["id"])
    with pytest.raises(IntegrityError), transaction.atomic():
        Stall.objects.create(layout=layout, label="a1", x=50, y=0, width=5, height=5, price_minor=0)


@pytest.mark.django_db
@pytest.mark.parametrize("label", ["", "   ", "x" * 41, "A⁣1", "A\n1"])
def test_bad_labels(world, label):
    response = world.client["OWNER"].put(world.url(), body([stall(label)]))
    assert response.status_code in (400, 422)
    assert not StallLayout.objects.exists()


# --- Geometry -----------------------------------------------------------------------------


def test_rect_overlap_rules():
    a = Rect(0, 0, 10, 10)
    assert not a.overlaps(Rect(10, 0, 10, 10))  # shares the right edge
    assert not a.overlaps(Rect(0, 10, 10, 10))  # shares the bottom edge
    assert not a.overlaps(Rect(10, 10, 5, 5))  # touches a corner
    assert a.overlaps(Rect(9, 9, 5, 5))
    assert a.overlaps(Rect(2, 2, 2, 2))  # contained
    assert overlapping_pairs([Rect(0, 0, 10, 10), Rect(20, 0, 5, 5), Rect(5, 5, 10, 10)]) == [
        (0, 2)
    ]


@pytest.mark.django_db
def test_touching_stalls_are_allowed(world):
    stalls = [stall("A1"), stall("A2", x=10), stall("B1", y=10), stall("Edge", x=90, y=50)]
    assert len(_create(world, stalls)["stalls"]) == 4


@pytest.mark.django_db
def test_overlapping_stalls_are_rejected(world):
    error = _problems(
        world.client["OWNER"].put(world.url(), body([stall("A1"), stall("A2", x=5, y=5)]))
    )
    assert error["details"][0]["message"] == "Overlaps stall 'A1'."
    assert error["details"][0]["index"] == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "bad",
    [
        {"x": 91},  # 91 + 10 > 100
        {"y": 51},  # 51 + 10 > 60
        {"width": 101},
    ],
)
def test_stalls_must_fit_the_canvas(world, bad):
    error = _problems(world.client["OWNER"].put(world.url(), body([stall(**bad)])))
    assert error["details"][0]["message"] == "The stall must fit inside the canvas."


@pytest.mark.django_db
@pytest.mark.parametrize("bad", [{"width": 0}, {"height": -1}, {"x": -1}])
def test_dimensions_must_be_positive(world, bad):
    assert world.client["OWNER"].put(world.url(), body([stall(**bad)])).status_code == 422


@pytest.mark.django_db
@pytest.mark.parametrize("canvas", [{"canvas_width": 0}, {"canvas_height": 10_001}])
def test_canvas_bounds(world, canvas):
    error = _problems(world.client["OWNER"].put(world.url(), body(**canvas)))
    assert error["code"] == "layout_invalid"


@pytest.mark.django_db
def test_physical_dimensions(world):
    ok = stall(physical_width="10", physical_depth="12.5", physical_unit="FT")
    data = _create(world, [ok])
    assert (data["stalls"][0]["physical_depth"], data["stalls"][0]["physical_unit"]) == (
        "12.50",
        "FT",
    )
    for bad in (
        {"physical_width": "10", "physical_depth": None, "physical_unit": None},
        {"physical_width": "10", "physical_depth": "10", "physical_unit": None},
        {"physical_width": "0", "physical_depth": "10", "physical_unit": "M"},
        {"physical_width": "1.555", "physical_depth": "10", "physical_unit": "M"},
    ):
        stalls = [{**_ins(data["stalls"])[0], **bad}]
        response = world.client["OWNER"].put(world.url(), body(stalls, revision=data["revision"]))
        assert response.status_code == 400, bad
    assert (
        world.client["OWNER"].put(world.url(), body([stall(physical_unit="YD")])).status_code == 422
    )


# --- Money --------------------------------------------------------------------------------


@pytest.mark.django_db
def test_prices_are_integer_minor_units(world):
    assert world.client["OWNER"].put(world.url(), body([stall(price=12.5)])).status_code == 422
    assert world.client["OWNER"].put(world.url(), body([stall(price="12.50")])).status_code == 422
    assert world.client["OWNER"].put(world.url(), body([stall(price=-1)])).status_code == 422
    error = _problems(world.client["OWNER"].put(world.url(), body([stall(price=1_000_000_001)])))
    assert error["details"][0]["field"] == "price_minor"
    data = _create(world, [stall(price=0), stall("A2", x=10, price=1_000_000_000)])
    assert [s["price_minor"] for s in data["stalls"]] == [0, 1_000_000_000]


@pytest.mark.django_db
@pytest.mark.parametrize(("currency", "exponent"), [("usd", 2), ("JPY", 0), ("EUR", 2)])
def test_supported_currencies(world, currency, exponent):
    data = _create(world, currency=currency)
    assert (data["currency"], data["currency_exponent"]) == (currency.upper(), exponent)


@pytest.mark.django_db
@pytest.mark.parametrize("currency", ["XYZ", "US", "BTC"])
def test_unsupported_currencies(world, currency):
    error = _problems(world.client["OWNER"].put(world.url(), body(currency=currency)))
    assert error["details"][0]["field"] == "currency"


@pytest.mark.django_db
def test_currency_is_a_database_constraint(world):
    data = _create(world)
    with pytest.raises(IntegrityError), transaction.atomic():
        StallLayout.objects.filter(pk=data["id"]).update(currency="XYZ")


# --- Publication ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_publish_and_unpublish_visibility(api, world):
    data = _create(world)
    assert api.get(world.public()).status_code == 404
    published = (
        world.client["ADMIN"].post(f"{world.url()}/publish", {"expected_revision": 1}).json()
    )
    assert published["published"] is True
    assert api.get(world.public()).status_code == 200
    # Publishing again is harmless.
    again = world.client["OWNER"].post(f"{world.url()}/publish", {"expected_revision": 1})
    assert again.json()["published_at"] == published["published_at"]
    # Published layouts must be unpublished before editing.
    response = world.client["OWNER"].put(world.url(), body(_ins(data["stalls"]), revision=1))
    assert response.json()["error"]["code"] == "layout_published"
    world.client["OWNER"].post(f"{world.url()}/unpublish")
    assert api.get(world.public()).status_code == 404
    assert (
        world.client["OWNER"].put(world.url(), body(_ins(data["stalls"]), revision=1)).status_code
        == 200
    )


@pytest.mark.django_db
def test_publish_needs_the_current_revision(world):
    _create(world)
    response = world.client["OWNER"].post(f"{world.url()}/publish", {"expected_revision": 7})
    assert response.json()["error"]["code"] == "stale_revision"


@pytest.mark.django_db
def test_publish_needs_an_enabled_stall(world):
    _create(world, [stall(enabled=False)])
    response = world.client["OWNER"].post(f"{world.url()}/publish", {"expected_revision": 1})
    assert response.json()["error"]["code"] == "layout_empty"
    response = world.client["OWNER"].post(
        f"{world.url(world.second)}/publish", {"expected_revision": 1}
    )
    assert response.json()["error"]["code"] == "layout_not_found"


@pytest.mark.django_db
def test_publish_needs_an_upcoming_scheduled_date(world):
    _create(world)
    market_services.cancel_occurrence(world.owner, world.org.pk, world.market.pk, world.first.pk)
    response = world.client["OWNER"].post(f"{world.url()}/publish", {"expected_revision": 1})
    assert response.json()["error"]["code"] == "occurrence_unavailable"


@pytest.mark.django_db
def test_archived_market_layout_cannot_change(world):
    data = _create(world)
    market_services.archive_market(world.owner, world.org.pk, world.market.pk)
    response = world.client["OWNER"].put(world.url(), body(_ins(data["stalls"]), revision=1))
    assert response.json()["error"]["code"] == "market_archived"


@pytest.mark.django_db
def test_public_layout_hidden_for_unavailable_dates(api, world):
    _create(world)
    world.client["OWNER"].post(f"{world.url()}/publish", {"expected_revision": 1})
    assert api.get(world.public()).status_code == 200
    market_services.cancel_occurrence(world.owner, world.org.pk, world.market.pk, world.first.pk)
    assert api.get(world.public()).status_code == 404


@pytest.mark.django_db
def test_public_layout_hidden_for_past_dates_and_archived_markets(api, world):
    _create(world)
    world.client["OWNER"].post(f"{world.url()}/publish", {"expected_revision": 1})
    EventOccurrence.objects.filter(pk=world.first.pk).update(
        starts_at=timezone.now() - timedelta(hours=5), ends_at=timezone.now() - timedelta(hours=1)
    )
    assert api.get(world.public()).status_code == 404
    EventOccurrence.objects.filter(pk=world.first.pk).update(
        starts_at=_future(5), ends_at=_future(5, 12)
    )
    assert api.get(world.public()).status_code == 200
    market_services.archive_market(world.owner, world.org.pk, world.market.pk)
    assert api.get(world.public()).status_code == 404


# --- Public response ---------------------------------------------------------------------


@pytest.mark.django_db
def test_public_layout_privacy_and_disabled_stalls(api, world):
    _create(
        world,
        [stall("A1", description="Corner"), stall("A2", x=10, enabled=False)],
        currency="JPY",
    )
    world.client["OWNER"].post(f"{world.url()}/publish", {"expected_revision": 1})
    data = api.get(world.public()).json()
    assert set(data) == {
        "occurrence_id",
        "market_id",
        "canvas_width",
        "canvas_height",
        "currency",
        "currency_exponent",
        "stalls",
    }
    assert (data["currency"], data["currency_exponent"]) == ("JPY", 0)
    assert [(s["label"], s["offered"]) for s in data["stalls"]] == [("A1", True), ("A2", False)]
    assert set(data["stalls"][0]) == {
        "id",
        "label",
        "description",
        "x",
        "y",
        "width",
        "height",
        "physical_width",
        "physical_depth",
        "physical_unit",
        "offered",
        "price_minor",
    }
