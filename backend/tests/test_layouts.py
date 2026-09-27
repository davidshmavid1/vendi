from datetime import datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from layouts.geometry import Rect, overlapping_pairs
from layouts.models import LayoutVersion, OccurrenceLayout, Stall, StallOffer
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


def stall(label="A1", x=0, y=0, width=10, height=10, **extra):
    return {"label": label, "x": x, "y": y, "width": width, "height": height, **extra}


def plan(stalls=None, **extra):
    return {
        "canvas_width": 100,
        "canvas_height": 60,
        "stalls": stalls if stalls is not None else [stall()],
        **extra,
    }


def _ins(stalls):
    """Response stalls back into request stalls."""
    keep = ("id", "label", "description", "x", "y", "width", "height")
    keep += ("physical_width", "physical_depth", "physical_unit")
    return [{k: s[k] for k in keep} for s in stalls]


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
    other_market = market_services.create_market(
        owner, org.pk, name="Sunday", market_type="POPUP", timezone="America/Chicago", **VENUE
    )
    first = market_services.create_occurrence(
        owner, org.pk, market.pk, starts_at=_future(5), ends_at=_future(5, 12)
    )
    second = market_services.create_occurrence(
        owner, org.pk, market.pk, starts_at=_future(12), ends_at=_future(12, 12)
    )
    market_services.publish_market(owner, org.pk, market.pk)
    base = f"/organizations/{org.pk}/markets/{market.pk}"
    return SimpleNamespace(
        org=org,
        other_org=other_org,
        owner=owner,
        market=market,
        other_market=other_market,
        first=first,
        second=second,
        versions=f"{base}/layout-versions",
        date=lambda occurrence=first: f"{base}/occurrences/{occurrence.pk}/layout",
        public=lambda occurrence=first: f"/public/occurrences/{occurrence.pk}/layout",
        client={
            "OWNER": as_user(owner),
            "ADMIN": as_user(admin),
            "STAFF": as_user(staff),
            "OUTSIDER": as_user(outsider),
            "VENDOR": as_user(vendor),
        },
    )


def _version(world, stalls=None, client="OWNER", **extra):
    response = world.client[client].post(world.versions, plan(stalls, **extra))
    assert response.status_code == 201, response.content
    return response.json()


def _offers(version, price=2500, **overrides):
    return [
        {
            "stall_id": s["id"],
            "price_minor": price,
            "enabled": True,
            **overrides.get(s["label"], {}),
        }
        for s in version["stalls"]
    ]


def _assign(world, version, occurrence=None, revision=None, currency="USD", offers=None):
    occurrence = occurrence or world.first
    body = {
        "expected_revision": revision,
        "layout_version_id": version["id"],
        "currency": currency,
        "offers": offers if offers is not None else _offers(version),
    }
    return world.client["OWNER"].put(world.date(occurrence), body)


def _problems(response):
    assert response.status_code == 400, response.content
    return response.json()["error"]


# --- Permissions and isolation ---------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("role", "write", "read"),
    [
        ("OWNER", 201, 200),
        ("ADMIN", 201, 200),
        ("STAFF", 403, 200),
        ("OUTSIDER", 404, 404),
        ("VENDOR", 404, 404),
    ],
)
def test_version_role_permissions(world, role, write, read):
    assert world.client[role].post(world.versions, plan()).status_code == write
    assert world.client[role].get(world.versions).status_code == read
    version = _version(world)
    assert world.client[role].get(f"{world.versions}/{version['id']}").status_code == read


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["STAFF", "OUTSIDER", "VENDOR"])
def test_only_managers_change_dates(world, role):
    version = _version(world)
    body = {
        "expected_revision": None,
        "layout_version_id": version["id"],
        "currency": "USD",
        "offers": _offers(version),
    }
    assert world.client[role].put(world.date(), body).status_code in (403, 404)
    assert _assign(world, version).status_code == 200
    staff_read = world.client[role].get(world.date()).status_code
    assert staff_read == (200 if role == "STAFF" else 404)
    publish = world.client[role].post(f"{world.date()}/publish", {"expected_revision": 1})
    assert publish.status_code in (403, 404)
    assert world.client[role].post(f"{world.date()}/unpublish").status_code in (403, 404)


@pytest.mark.django_db
def test_other_organization_cannot_reach_anything(world):
    version = _version(world)
    _assign(world, version)
    foreign = f"/organizations/{world.other_org.pk}/markets/{world.market.pk}"
    outsider = world.client["OUTSIDER"]
    assert outsider.get(f"{foreign}/layout-versions").status_code == 404
    assert outsider.get(f"{foreign}/occurrences/{world.first.pk}/layout").status_code == 404
    assert outsider.post(f"{foreign}/layout-versions", plan()).status_code == 404


@pytest.mark.django_db
def test_versions_belong_to_their_market(world):
    other = world.client["OWNER"].post(
        f"/organizations/{world.org.pk}/markets/{world.other_market.pk}/layout-versions", plan()
    )
    other = other.json()
    # Can't be read through, or assigned to a date of, another market.
    assert world.client["OWNER"].get(f"{world.versions}/{other['id']}").status_code == 404
    error = _problems(_assign(world, other))
    assert error["code"] == "layout_version_invalid"
    assert world.client["OWNER"].post(world.versions, plan(copy_of=other["id"])).status_code == 404


@pytest.mark.django_db
def test_client_cannot_set_internals(world):
    assert world.client["OWNER"].post(world.versions, plan(market_id=1)).status_code == 422
    assert world.client["OWNER"].post(world.versions, plan(locked_at=None)).status_code == 422
    version = _version(world)
    body = {"expected_revision": 1, **plan(_ins(version["stalls"])), "number": 9}
    response = world.client["OWNER"].put(f"{world.versions}/{version['id']}", body)
    assert response.status_code == 422
    offers = [{**o, "currency": "EUR"} for o in _offers(version)]
    assert _assign(world, version, offers=offers).status_code == 422


@pytest.mark.django_db
def test_auth_and_csrf(api, world):
    assert api.post(world.versions, plan()).status_code == 401
    response = world.client["OWNER"].post(world.versions, plan(), csrf=False)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"
    version = _version(world)
    body = {
        "expected_revision": None,
        "layout_version_id": version["id"],
        "currency": "USD",
        "offers": _offers(version),
    }
    assert world.client["OWNER"].put(world.date(), body, csrf=False).status_code == 403
    assert not OccurrenceLayout.objects.exists()


# --- Versions: numbering, editing, revisions -----------------------------------------------


@pytest.mark.django_db
def test_versions_are_numbered_per_market(world):
    assert [_version(world)["number"] for _ in range(3)] == [1, 2, 3]
    listing = world.client["STAFF"].get(world.versions).json()["items"]
    assert [(v["number"], v["stall_count"], v["locked"]) for v in listing] == [
        (1, 1, False),
        (2, 1, False),
        (3, 1, False),
    ]


@pytest.mark.django_db
def test_draft_edits_and_stale_revisions(world):
    version = _version(world, [stall("A1"), stall("A2", x=10)])
    url = f"{world.versions}/{version['id']}"
    stalls = _ins(version["stalls"])
    stalls[1] |= {"label": "Corner", "x": 40}
    stalls.append(stall("A3", x=20))
    updated = world.client["ADMIN"].put(url, {"expected_revision": 1, **plan(stalls)}).json()
    assert updated["revision"] == 2
    assert [s["id"] for s in updated["stalls"]][:2] == [s["id"] for s in version["stalls"]]
    assert [s["label"] for s in updated["stalls"]] == ["A1", "Corner", "A3"]
    stale = world.client["OWNER"].put(url, {"expected_revision": 1, **plan(stalls)})
    assert stale.status_code == 409
    assert stale.json()["error"] == {
        **stale.json()["error"],
        "code": "stale_revision",
        "details": [{"current_revision": 2}],
    }


@pytest.mark.django_db
def test_invalid_stall_leaves_version_unchanged(world):
    version = _version(world, [stall("A1"), stall("A2", x=10)])
    stalls = _ins(version["stalls"])
    stalls[0]["label"] = "Renamed"
    stalls.append(stall("A3", x=95))  # sticks out of the 100-wide canvas
    url = f"{world.versions}/{version['id']}"
    error = _problems(world.client["OWNER"].put(url, {"expected_revision": 1, **plan(stalls)}))
    assert error["code"] == "layout_invalid"
    assert error["details"][0]["index"] == 2
    saved = LayoutVersion.objects.get()
    assert saved.revision == 1
    assert sorted(saved.stalls.values_list("label", flat=True)) == ["A1", "A2"]


@pytest.mark.django_db
def test_stalls_cannot_be_removed_or_moved_between_versions(world):
    version = _version(world, [stall("A1"), stall("A2", x=10)])
    other = _version(world, [stall("B1")])
    url = f"{world.versions}/{version['id']}"
    missing = _ins(version["stalls"])[:1]
    error = _problems(world.client["OWNER"].put(url, {"expected_revision": 1, **plan(missing)}))
    assert error["code"] == "stall_mismatch"
    foreign = [*_ins(version["stalls"]), {**stall("B1", x=50), "id": other["stalls"][0]["id"]}]
    error = _problems(world.client["OWNER"].put(url, {"expected_revision": 1, **plan(foreign)}))
    assert error["code"] == "stall_mismatch"
    assert Stall.objects.get(pk=other["stalls"][0]["id"]).layout_version_id == other["id"]


@pytest.mark.django_db
def test_labels_can_be_swapped(world):
    version = _version(world, [stall("A1"), stall("A2", x=10)])
    stalls = _ins(version["stalls"])
    stalls[0]["label"], stalls[1]["label"] = "a2", "A1"
    url = f"{world.versions}/{version['id']}"
    response = world.client["OWNER"].put(url, {"expected_revision": 1, **plan(stalls)})
    assert [s["label"] for s in response.json()["stalls"]] == ["a2", "A1"]


@pytest.mark.django_db
@pytest.mark.parametrize("second", ["A1", "a1", " A1 "])
def test_labels_are_unique_case_insensitively(world, second):
    response = world.client["OWNER"].post(world.versions, plan([stall("A1"), stall(second, x=20)]))
    assert _problems(response)["details"] == [
        {
            "index": 1,
            "stall_id": None,
            "field": "label",
            "message": "Another stall already uses this label.",
        }
    ]


@pytest.mark.django_db
def test_label_uniqueness_is_a_database_constraint(world):
    version = LayoutVersion.objects.get(pk=_version(world)["id"])
    with pytest.raises(IntegrityError), transaction.atomic():
        Stall.objects.create(layout_version=version, label="a1", x=50, y=0, width=5, height=5)


@pytest.mark.django_db
@pytest.mark.parametrize("label", ["", "   ", "x" * 41, "A⁣1", "A\n1"])
def test_bad_labels(world, label):
    response = world.client["OWNER"].post(world.versions, plan([stall(label)]))
    assert response.status_code in (400, 422)
    assert not LayoutVersion.objects.exists()


# --- Geometry ----------------------------------------------------------------------------


def test_rect_overlap_rules():
    a = Rect(0, 0, 10, 10)
    assert not a.overlaps(Rect(10, 0, 10, 10))  # shares the right edge
    assert not a.overlaps(Rect(0, 10, 10, 10))  # shares the bottom edge
    assert not a.overlaps(Rect(10, 10, 5, 5))  # touches a corner
    assert a.overlaps(Rect(9, 9, 5, 5))
    assert a.overlaps(Rect(2, 2, 2, 2))  # contained
    rects = [Rect(0, 0, 10, 10), Rect(20, 0, 5, 5), Rect(5, 5, 10, 10)]
    assert overlapping_pairs(rects) == [(0, 2)]


@pytest.mark.django_db
def test_touching_stalls_are_allowed(world):
    stalls = [stall("A1"), stall("A2", x=10), stall("B1", y=10), stall("Edge", x=90, y=50)]
    assert len(_version(world, stalls)["stalls"]) == 4


@pytest.mark.django_db
def test_overlapping_stalls_are_rejected(world):
    response = world.client["OWNER"].post(
        world.versions, plan([stall("A1"), stall("A2", x=5, y=5)])
    )
    error = _problems(response)
    assert error["details"][0]["message"] == "Overlaps stall 'A1'."
    assert error["details"][0]["index"] == 1


@pytest.mark.django_db
@pytest.mark.parametrize("bad", [{"x": 91}, {"y": 51}, {"width": 101}])
def test_stalls_must_fit_the_canvas(world, bad):
    error = _problems(world.client["OWNER"].post(world.versions, plan([stall(**bad)])))
    assert error["details"][0]["message"] == "The stall must fit inside the canvas."


@pytest.mark.django_db
@pytest.mark.parametrize("bad", [{"width": 0}, {"height": -1}, {"x": -1}])
def test_dimensions_must_be_positive(world, bad):
    assert world.client["OWNER"].post(world.versions, plan([stall(**bad)])).status_code == 422


@pytest.mark.django_db
@pytest.mark.parametrize("canvas", [{"canvas_width": 0}, {"canvas_height": 10_001}])
def test_canvas_bounds(world, canvas):
    assert _problems(world.client["OWNER"].post(world.versions, plan(**canvas)))["code"] == (
        "layout_invalid"
    )


@pytest.mark.django_db
def test_physical_dimensions(world):
    ok = stall(physical_width="10", physical_depth="12.5", physical_unit="FT")
    data = _version(world, [ok])
    assert (data["stalls"][0]["physical_depth"], data["stalls"][0]["physical_unit"]) == (
        "12.50",
        "FT",
    )
    for bad in (
        {"physical_width": "10"},
        {"physical_width": "10", "physical_depth": "10"},
        {"physical_width": "0", "physical_depth": "10", "physical_unit": "M"},
        {"physical_width": "1.555", "physical_depth": "10", "physical_unit": "M"},
    ):
        assert world.client["OWNER"].post(world.versions, plan([stall(**bad)])).status_code == 400
    response = world.client["OWNER"].post(world.versions, plan([stall(physical_unit="YD")]))
    assert response.status_code == 422


# --- Locking and copies -------------------------------------------------------------------


@pytest.mark.django_db
def test_version_locks_when_a_date_uses_it(world):
    version = _version(world)
    assert _assign(world, version).status_code == 200
    detail = world.client["STAFF"].get(f"{world.versions}/{version['id']}").json()
    assert detail["locked"] is True
    body = {"expected_revision": detail["revision"], **plan(_ins(detail["stalls"]))}
    response = world.client["OWNER"].put(f"{world.versions}/{version['id']}", body)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "layout_version_locked"


@pytest.mark.django_db
def test_copy_makes_an_editable_version_and_leaves_dates_alone(world):
    v1 = _version(world, [stall("A1"), stall("A2", x=10)])
    _assign(world, v1, world.first)
    _assign(world, v1, world.second)
    copy = world.client["OWNER"].post(world.versions, plan(copy_of=v1["id"])).json()
    assert (copy["number"], copy["locked"], copy["based_on_id"]) == (2, False, v1["id"])
    assert [s["label"] for s in copy["stalls"]] == ["A1", "A2"]
    assert {s["id"] for s in copy["stalls"]}.isdisjoint({s["id"] for s in v1["stalls"]})
    stalls = _ins(copy["stalls"])
    stalls[0]["x"] = 50
    world.client["OWNER"].put(
        f"{world.versions}/{copy['id']}", {"expected_revision": 1, **plan(stalls)}
    )
    # Move only the second date to the copy.
    second = world.client["OWNER"].get(world.date(world.second)).json()
    response = _assign(world, copy, world.second, revision=second["revision"])
    assert response.json()["layout_version"]["id"] == copy["id"]
    first = world.client["OWNER"].get(world.date(world.first)).json()
    assert first["layout_version"]["id"] == v1["id"]
    assert first["layout_version"]["stalls"][0]["x"] == 0


# --- Offers ------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_offers_cover_exactly_the_selected_version(world):
    version = _version(world, [stall("A1"), stall("A2", x=10)])
    other = _version(world, [stall("B1")])
    error = _problems(_assign(world, version, offers=_offers(version)[:1]))
    assert (error["code"], error["details"][0]["message"]) == (
        "offers_invalid",
        "Every stall in the layout needs an offer.",
    )
    foreign = [*_offers(version), *_offers(other)]
    error = _problems(_assign(world, version, offers=foreign))
    assert error["details"][0]["message"] == "This stall isn't in the selected layout."
    duplicate = [*_offers(version), _offers(version)[0]]
    assert _problems(_assign(world, version, offers=duplicate))["details"][0]["message"] == (
        "Listed twice."
    )
    assert not OccurrenceLayout.objects.exists()
    # A failed save doesn't lock the version either.
    assert LayoutVersion.objects.get(pk=version["id"]).locked_at is None


@pytest.mark.django_db
def test_one_offer_per_occurrence_and_stall(world):
    version = _version(world)
    _assign(world, version)
    offer = StallOffer.objects.get()
    with pytest.raises(IntegrityError), transaction.atomic():
        StallOffer.objects.create(
            occurrence=offer.occurrence, stall=offer.stall, price_minor=1, currency="USD"
        )


@pytest.mark.django_db
def test_date_revisions_and_stale_saves(world):
    version = _version(world)
    created = _assign(world, version).json()
    assert (created["revision"], created["currency"], created["currency_exponent"]) == (1, "USD", 2)
    offer_id = created["offers"][0]["id"]
    updated = _assign(world, version, revision=1, offers=_offers(version, price=900)).json()
    assert updated["revision"] == 2
    assert updated["offers"][0] == {
        "id": offer_id,
        "stall_id": version["stalls"][0]["id"],
        "price_minor": 900,
        "currency": "USD",
        "enabled": True,
    }
    stale = _assign(world, version, revision=1)
    assert stale.json()["error"]["code"] == "stale_revision"
    assert _assign(world, version).json()["error"]["code"] == "stale_revision"


@pytest.mark.django_db
def test_prices_are_integer_minor_units(world):
    version = _version(world)
    for price in (12.5, "12.50", -1):
        assert _assign(world, version, offers=_offers(version, price=price)).status_code == 422
    error = _problems(_assign(world, version, offers=_offers(version, price=1_000_000_001)))
    assert error["details"][0]["field"] == "price_minor"
    assert _assign(world, version, offers=_offers(version, price=0)).status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize(("currency", "exponent"), [("usd", 2), ("JPY", 0), ("EUR", 2)])
def test_supported_currencies(world, currency, exponent):
    data = _assign(world, _version(world), currency=currency).json()
    assert (data["currency"], data["currency_exponent"]) == (currency.upper(), exponent)


@pytest.mark.django_db
@pytest.mark.parametrize("currency", ["XYZ", "US", "BTC"])
def test_unsupported_currencies(world, currency):
    error = _problems(_assign(world, _version(world), currency=currency))
    assert error["details"][0]["field"] == "currency"


@pytest.mark.django_db
def test_offer_constraints_in_the_database(world):
    _assign(world, _version(world))
    for change in ({"currency": "XYZ"}, {"price_minor": -1}):
        with pytest.raises(IntegrityError), transaction.atomic():
            StallOffer.objects.update(**change)


@pytest.mark.django_db
def test_switching_versions_keeps_old_offers_out_of_view(world):
    v1 = _version(world, [stall("A1")])
    v2 = _version(world, [stall("A1"), stall("B1", x=20)])
    _assign(world, v1)
    data = _assign(world, v2, revision=1, offers=_offers(v2, price=700)).json()
    assert [o["stall_id"] for o in data["offers"]] == [s["id"] for s in v2["stalls"]]
    assert StallOffer.objects.count() == 3  # the v1 offer is kept, not shown


@pytest.mark.django_db
def test_archived_market_layouts_cannot_change(world):
    version = _version(world)
    _assign(world, version)
    market_services.archive_market(world.owner, world.org.pk, world.market.pk)
    assert world.client["OWNER"].post(world.versions, plan()).json()["error"]["code"] == (
        "market_archived"
    )
    assert _assign(world, version, revision=1).json()["error"]["code"] == "market_archived"


# --- Publication -------------------------------------------------------------------------------


@pytest.mark.django_db
def test_publish_and_unpublish_visibility(api, world):
    version = _version(world)
    _assign(world, version)
    assert api.get(world.public()).status_code == 404
    published = world.client["ADMIN"].post(f"{world.date()}/publish", {"expected_revision": 1})
    assert published.json()["published"] is True
    assert api.get(world.public()).status_code == 200
    again = world.client["OWNER"].post(f"{world.date()}/publish", {"expected_revision": 1})
    assert again.json()["published_at"] == published.json()["published_at"]
    # Published: must unpublish before changing prices or the version.
    assert _assign(world, version, revision=1).json()["error"]["code"] == "layout_published"
    world.client["OWNER"].post(f"{world.date()}/unpublish")
    assert api.get(world.public()).status_code == 404
    assert _assign(world, version, revision=1).status_code == 200


@pytest.mark.django_db
def test_publish_needs_current_revision_and_an_offer(world):
    version = _version(world)
    _assign(world, version, offers=_offers(version, A1={"enabled": False}))
    response = world.client["OWNER"].post(f"{world.date()}/publish", {"expected_revision": 7})
    assert response.json()["error"]["code"] == "stale_revision"
    response = world.client["OWNER"].post(f"{world.date()}/publish", {"expected_revision": 1})
    assert response.json()["error"]["code"] == "layout_empty"
    response = world.client["OWNER"].post(
        f"{world.date(world.second)}/publish", {"expected_revision": 1}
    )
    assert response.json()["error"]["code"] == "layout_not_found"


@pytest.mark.django_db
def test_publish_needs_an_upcoming_scheduled_date(world):
    _assign(world, _version(world))
    market_services.cancel_occurrence(world.owner, world.org.pk, world.market.pk, world.first.pk)
    response = world.client["OWNER"].post(f"{world.date()}/publish", {"expected_revision": 1})
    assert response.json()["error"]["code"] == "occurrence_unavailable"


@pytest.mark.django_db
def test_public_layout_hidden_for_cancelled_past_and_archived(api, world):
    _assign(world, _version(world))
    world.client["OWNER"].post(f"{world.date()}/publish", {"expected_revision": 1})
    assert api.get(world.public()).status_code == 200
    EventOccurrence.objects.filter(pk=world.first.pk).update(
        starts_at=timezone.now() - timedelta(hours=5), ends_at=timezone.now() - timedelta(hours=1)
    )
    assert api.get(world.public()).status_code == 404
    EventOccurrence.objects.filter(pk=world.first.pk).update(
        starts_at=_future(5), ends_at=_future(5, 12)
    )
    assert api.get(world.public()).status_code == 200
    market_services.cancel_occurrence(world.owner, world.org.pk, world.market.pk, world.first.pk)
    assert api.get(world.public()).status_code == 404


@pytest.mark.django_db
def test_public_layout_hidden_for_archived_market(api, world):
    _assign(world, _version(world))
    world.client["OWNER"].post(f"{world.date()}/publish", {"expected_revision": 1})
    market_services.archive_market(world.owner, world.org.pk, world.market.pk)
    assert api.get(world.public()).status_code == 404


# --- Public response ---------------------------------------------------------------------


@pytest.mark.django_db
def test_public_layout_privacy_and_disabled_offers(api, world):
    version = _version(world, [stall("A1", description="Corner"), stall("A2", x=10)])
    offers = _offers(version, price=2500, A2={"enabled": False})
    _assign(world, version, currency="JPY", offers=offers)
    world.client["OWNER"].post(f"{world.date()}/publish", {"expected_revision": 1})
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
    offer_ids = dict(StallOffer.objects.values_list("stall__label", "pk"))
    assert [
        (s["label"], s["offered"], s["price_minor"], s["offer_id"]) for s in data["stalls"]
    ] == [
        ("A1", True, 2500, offer_ids["A1"]),
        ("A2", False, None, None),
    ]
    assert set(data["stalls"][0]) == {
        "id",
        "offer_id",
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
