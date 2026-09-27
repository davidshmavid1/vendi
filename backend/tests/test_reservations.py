from contextlib import contextmanager
from datetime import datetime, time, timedelta
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.utils import timezone

from applications import services as application_services
from applications.models import Application
from layouts import services as layout_services
from layouts.models import OccurrenceLayout, StallOffer
from markets import services as market_services
from markets.models import EventOccurrence
from moderation import services as moderation_services
from organizations import services as org_services
from reservations import services
from reservations.models import Reservation, ReservationStatus
from vendors import services as vendor_services
from vendors.models import VendorMembership, VendorRole

CHICAGO = ZoneInfo("America/Chicago")
VENUE = {"venue_name": "Park", "address_line1": "1 Main", "city": "Springfield", "country": "US"}
HOLD = timedelta(minutes=15)


def _future(days, hour=8):
    day = timezone.now().astimezone(CHICAGO).date() + timedelta(days=days)
    return datetime.combine(day, time(hour), tzinfo=CHICAGO)


@contextmanager
def at(moment):
    """Controlled time: everything that asks django.utils.timezone for "now"."""
    with mock.patch("django.utils.timezone.now", return_value=moment):
        yield


def _stalls(n):
    return [
        {
            "label": f"A{i + 1}",
            "description": "",
            "x": i * 10,
            "y": 0,
            "width": 10,
            "height": 10,
            "physical_width": None,
            "physical_depth": None,
            "physical_unit": None,
        }
        for i in range(n)
    ]


def _approve(owner, org, market, occurrence, vendor, business):
    application = application_services.submit(
        vendor,
        business.pk,
        occurrence_id=occurrence.pk,
        questions_version=1,
        answers={},
    )
    return application_services.decide(owner, org.pk, application.pk, approve=True)


@pytest.fixture
def world(make_user, as_user):
    owner = make_user("owner@example.com")
    vendor_a, member_a = make_user("a@example.com"), make_user("a-member@example.com")
    vendor_b, outsider = make_user("b@example.com"), make_user("outsider@example.com")
    org = org_services.create_organization(owner, name="Riverside").organization
    market = market_services.create_market(
        owner, org.pk, name="Saturday", market_type="POPUP", timezone="America/Chicago", **VENUE
    )
    occurrence = market_services.create_occurrence(
        owner, org.pk, market.pk, starts_at=_future(5), ends_at=_future(5, 12)
    )
    other_date = market_services.create_occurrence(
        owner, org.pk, market.pk, starts_at=_future(12), ends_at=_future(12, 12)
    )
    market_services.publish_market(owner, org.pk, market.pk)
    for date in (occurrence, other_date):
        application_services.configure_intake(
            owner,
            org.pk,
            market.pk,
            date.pk,
            enabled=True,
            opens_at=None,
            closes_at=None,
            instructions="",
            questions=[],
        )
    version, stalls = layout_services.create_version(
        owner, org.pk, market.pk, canvas_width=100, canvas_height=50, stalls=_stalls(3)
    )
    for date in (occurrence, other_date):
        layout_services.save_date_layout(
            owner,
            org.pk,
            market.pk,
            date.pk,
            expected_revision=None,
            layout_version_id=version.pk,
            currency="USD",
            offers=[{"stall_id": s.pk, "price_minor": 2500, "enabled": True} for s in stalls],
        )
        layout_services.publish_date_layout(owner, org.pk, market.pk, date.pk, expected_revision=1)
    business_a = vendor_services.create_business(
        vendor_a, name="Bees", category="PRODUCE", contact_email="a@x.example"
    ).business
    VendorMembership.objects.create(business=business_a, user=member_a, role=VendorRole.MEMBER)
    business_b = vendor_services.create_business(
        vendor_b, name="Crafts", category="CRAFTS", contact_email="b@x.example"
    ).business
    app_a = _approve(owner, org, market, occurrence, vendor_a, business_a)
    app_b = _approve(owner, org, market, occurrence, vendor_b, business_b)
    offers = list(StallOffer.objects.filter(occurrence=occurrence).order_by("stall_id"))
    other_offers = list(StallOffer.objects.filter(occurrence=other_date).order_by("stall_id"))
    return SimpleNamespace(
        owner=owner,
        org=org,
        market=market,
        occurrence=occurrence,
        other_date=other_date,
        version=version,
        offers=offers,
        other_offers=other_offers,
        vendor_a=vendor_a,
        vendor_b=vendor_b,
        business_a=business_a,
        business_b=business_b,
        app_a=app_a,
        app_b=app_b,
        url_a=f"/vendors/{business_a.pk}/reservations",
        url_b=f"/vendors/{business_b.pk}/reservations",
        public=f"/public/occurrences/{occurrence.pk}/stall-availability",
        client={
            "A": as_user(vendor_a),
            "A_MEMBER": as_user(member_a),
            "B": as_user(vendor_b),
            "OUTSIDER": as_user(outsider),
            "ORG_OWNER": as_user(owner),
        },
    )


def _hold(world, who="A", offer=None, key="k1"):
    offer = offer or world.offers[0]
    url = world.url_a if who.startswith("A") else world.url_b
    return world.client[who].post(url, {"offer_id": offer.pk, "request_key": key})


def _held(world, who="A", offer=None, key="k1"):
    response = _hold(world, who, offer, key)
    assert response.status_code == 201, response.content
    return response.json()


def _rev(world):
    return OccurrenceLayout.objects.get(occurrence=world.occurrence).revision


def _layout_payload(world, version, enabled=True, price=2500, disable=()):
    stalls = version.stalls.order_by("pk")
    return dict(
        expected_revision=None,
        layout_version_id=version.pk,
        currency="USD",
        offers=[
            {"stall_id": s.pk, "price_minor": price, "enabled": enabled and s.pk not in disable}
            for s in stalls
        ],
    )


# --- Taking a hold ---------------------------------------------------------------------


@pytest.mark.django_db
def test_hold_snapshots_price_and_expires(world):
    now = timezone.now()
    with at(now):
        data = _held(world)
    assert data["status"] == "HELD"
    assert (data["price_minor"], data["currency"], data["currency_exponent"]) == (2500, "USD", 2)
    assert data["offer_id"] == world.offers[0].pk
    assert data["stall"]["label"] == "A1"
    assert data["application_id"] == world.app_a.pk
    row = Reservation.objects.get()
    assert row.expires_at == now + HOLD
    assert row.held_by == world.vendor_a
    assert "server_time" in data


@pytest.mark.django_db
def test_price_comes_from_the_offer_not_the_client(world):
    body = {"offer_id": world.offers[0].pk, "request_key": "k", "price_minor": 1}
    assert world.client["A"].post(world.url_a, body).status_code == 422
    assert not Reservation.objects.exists()


@pytest.mark.django_db
def test_snapshot_survives_price_changes(world):
    _held(world)
    layout_services.unpublish_date_layout(
        world.owner, world.org.pk, world.market.pk, world.occurrence.pk
    )
    payload = _layout_payload(world, world.version, price=9900) | {"expected_revision": _rev(world)}
    layout_services.save_date_layout(
        world.owner, world.org.pk, world.market.pk, world.occurrence.pk, **payload
    )
    assert StallOffer.objects.get(pk=world.offers[0].pk).price_minor == 9900
    assert world.client["A"].get(world.url_a).json()["items"][0]["price_minor"] == 2500


@pytest.mark.django_db
def test_hold_duration_is_configurable(world, settings):
    settings.RESERVATION_HOLD_SECONDS = 60
    now = timezone.now()
    with at(now):
        _held(world)
    assert Reservation.objects.get().expires_at == now + timedelta(seconds=60)


# --- Permissions ---------------------------------------------------------------------


@pytest.mark.django_db
def test_only_the_owner_holds_and_members_read(world):
    assert _hold(world, "A_MEMBER").status_code == 403
    assert (
        world.client["OUTSIDER"].post(world.url_a, {"offer_id": 1, "request_key": "k"}).status_code
        == 404
    )
    assert (
        world.client["ORG_OWNER"].post(world.url_a, {"offer_id": 1, "request_key": "k"}).status_code
        == 404
    )
    reservation = _held(world)
    for who in ("A", "A_MEMBER"):
        assert world.client[who].get(world.url_a).json()["items"][0]["id"] == reservation["id"]
        assert world.client[who].get(f"{world.url_a}/{reservation['id']}").status_code == 200
    for who in ("B", "OUTSIDER", "ORG_OWNER"):
        assert world.client[who].get(world.url_a).status_code == 404
    # Another business can't read or release it through its own URL either.
    assert world.client["B"].get(f"{world.url_b}/{reservation['id']}").status_code == 404
    assert world.client["B"].post(f"{world.url_b}/{reservation['id']}/release").status_code == 404
    assert (
        world.client["A_MEMBER"].post(f"{world.url_a}/{reservation['id']}/release").status_code
        == 403
    )
    assert Reservation.objects.get().status == "HELD"


@pytest.mark.django_db
def test_hold_needs_a_verified_account(world):
    world.vendor_a.email_verified_at = None
    world.vendor_a.save()
    assert _hold(world).json()["error"]["code"] == "email_not_verified"


@pytest.mark.django_db
def test_auth_and_csrf(api, world):
    body = {"offer_id": world.offers[0].pk, "request_key": "k"}
    assert api.post(world.url_a, body).status_code == 401
    response = world.client["A"].post(world.url_a, body, csrf=False)
    assert (response.status_code, response.json()["error"]["code"]) == (403, "csrf_failed")
    reservation = _held(world)
    release = world.client["A"].post(f"{world.url_a}/{reservation['id']}/release", csrf=False)
    assert release.status_code == 403
    assert Reservation.objects.get().status == "HELD"


@pytest.mark.django_db
def test_there_is_no_confirmation_endpoint(world):
    reservation = _held(world)
    for suffix in ("confirm", "pay"):
        response = world.client["A"].post(f"{world.url_a}/{reservation['id']}/{suffix}")
        assert response.status_code in (404, 405)
    body = {"offer_id": world.offers[0].pk, "request_key": "k2", "status": "CONFIRMED"}
    assert world.client["A"].post(world.url_a, body).status_code == 422
    assert Reservation.objects.get().status == "HELD"


# --- Eligibility -------------------------------------------------------------------------


@pytest.mark.django_db
def test_hold_needs_an_approved_application(world, make_user, as_user):
    vendor = make_user("c@example.com")
    business = vendor_services.create_business(
        vendor, name="Candles", category="CRAFTS", contact_email="c@x.example"
    ).business
    client = as_user(vendor)
    url = f"/vendors/{business.pk}/reservations"
    body = {"offer_id": world.offers[0].pk, "request_key": "k"}
    assert client.post(url, body).json()["error"]["code"] == "application_not_approved"
    application_services.submit(
        vendor, business.pk, occurrence_id=world.occurrence.pk, questions_version=1, answers={}
    )
    assert client.post(url, body).json()["error"]["code"] == "application_not_approved"
    # Approval for another date doesn't count.
    other = _hold(world, offer=world.other_offers[0])
    assert other.json()["error"]["code"] == "application_not_approved"


@pytest.mark.django_db
@pytest.mark.parametrize("target", ["account", "business"])
def test_restricted_vendors_cannot_hold_but_can_view_and_release(world, target):
    reservation = _held(world)
    moderation_services.create_restriction(
        world.owner,
        world.org.pk,
        account_id=world.vendor_a.pk if target == "account" else None,
        vendor_business_id=world.business_a.pk if target == "business" else None,
        reason="Private reason",
    )
    assert world.client["A"].get(world.url_a).json()["items"][0]["status"] == "HELD"
    released = world.client["A"].post(f"{world.url_a}/{reservation['id']}/release")
    assert released.json()["status"] == "RELEASED"
    response = _hold(world, key="k2")
    assert (response.status_code, response.json()["error"]["code"]) == (
        403,
        "participation_restricted",
    )
    assert "Private reason" not in response.content.decode()


@pytest.mark.django_db
def test_hold_needs_an_upcoming_scheduled_published_date(world):
    market_services.cancel_occurrence(
        world.owner, world.org.pk, world.market.pk, world.occurrence.pk
    )
    assert _hold(world).json()["error"]["code"] == "occurrence_unavailable"


@pytest.mark.django_db
def test_hold_refused_once_the_event_started(world):
    with at(world.occurrence.starts_at + timedelta(minutes=1)):
        assert _hold(world).json()["error"]["code"] == "occurrence_unavailable"


@pytest.mark.django_db
def test_hold_refused_for_archived_market(world):
    market_services.archive_market(world.owner, world.org.pk, world.market.pk)
    assert _hold(world).json()["error"]["code"] == "stall_not_available"


@pytest.mark.django_db
def test_hold_needs_a_published_layout_and_an_enabled_offer(world):
    layout_services.unpublish_date_layout(
        world.owner, world.org.pk, world.market.pk, world.occurrence.pk
    )
    assert _hold(world).json()["error"]["code"] == "stall_not_available"
    payload = _layout_payload(world, world.version, disable={world.offers[0].stall_id})
    layout_services.save_date_layout(
        world.owner,
        world.org.pk,
        world.market.pk,
        world.occurrence.pk,
        **payload | {"expected_revision": _rev(world)},
    )
    layout_services.publish_date_layout(
        world.owner,
        world.org.pk,
        world.market.pk,
        world.occurrence.pk,
        expected_revision=_rev(world),
    )
    assert _hold(world).json()["error"]["code"] == "stall_not_available"
    assert _hold(world, offer=world.offers[1]).status_code == 201


@pytest.mark.django_db
def test_offers_of_a_version_no_longer_used_are_not_holdable(world):
    layout_services.unpublish_date_layout(
        world.owner, world.org.pk, world.market.pk, world.occurrence.pk
    )
    copy, _ = layout_services.create_version(
        world.owner,
        world.org.pk,
        world.market.pk,
        canvas_width=0,
        canvas_height=0,
        stalls=[],
        copy_of=world.version.pk,
    )
    payload = _layout_payload(world, copy) | {"expected_revision": _rev(world)}
    layout_services.save_date_layout(
        world.owner, world.org.pk, world.market.pk, world.occurrence.pk, **payload
    )
    layout_services.publish_date_layout(
        world.owner,
        world.org.pk,
        world.market.pk,
        world.occurrence.pk,
        expected_revision=_rev(world),
    )
    # world.offers belong to the old version's stalls.
    assert _hold(world).json()["error"]["code"] == "stall_not_available"


@pytest.mark.django_db
def test_unknown_offer(world):
    body = {"offer_id": 999_999, "request_key": "k"}
    assert world.client["A"].post(world.url_a, body).status_code == 404


# --- Inventory rules -----------------------------------------------------------------------


@pytest.mark.django_db
def test_one_occupant_per_stall_and_one_stall_per_business(world):
    _held(world)
    taken = _hold(world, "B", key="b1")
    assert (taken.status_code, taken.json()["error"]["code"]) == (409, "stall_unavailable")
    second = _hold(world, offer=world.offers[1], key="k2")
    assert second.json()["error"]["code"] == "hold_exists"
    assert second.json()["error"]["details"][0]["offer_id"] == world.offers[0].pk
    assert _hold(world, "B", offer=world.offers[1], key="b2").status_code == 201


@pytest.mark.django_db
def test_switching_stalls_means_release_then_hold(world):
    first = _held(world)
    world.client["A"].post(f"{world.url_a}/{first['id']}/release")
    second = _held(world, offer=world.offers[1], key="k2")
    assert second["offer_id"] == world.offers[1].pk
    assert _hold(world, "B", key="b1").status_code == 201  # the first stall is free again


@pytest.mark.django_db
def test_expired_holds_stop_blocking_immediately(world):
    start = timezone.now()
    with at(start):
        reservation = _held(world)
    with at(start + HOLD - timedelta(seconds=1)):
        assert _hold(world, "B", key="b1").json()["error"]["code"] == "stall_unavailable"
    with at(start + HOLD):
        # No housekeeping ran: the new claim reclaims the lapsed hold itself.
        assert _hold(world, "B", key="b1").status_code == 201
    old = Reservation.objects.get(pk=reservation["id"])
    assert (old.status, old.expired_at) == ("EXPIRED", start + HOLD)


@pytest.mark.django_db
def test_lapsed_holds_read_as_expired_before_cleanup(world):
    start = timezone.now()
    with at(start):
        reservation = _held(world)
    with at(start + HOLD + timedelta(seconds=1)):
        data = world.client["A"].get(f"{world.url_a}/{reservation['id']}").json()
        assert data["status"] == "EXPIRED"
        assert world.client["A"].get(f"{world.url_a}?status=EXPIRED").json()["items"]
        assert world.client["A"].get(f"{world.url_a}?status=HELD").json()["items"] == []
        # The business may take a new hold (on any stall) right away.
        assert _hold(world, offer=world.offers[2], key="k2").status_code == 201
    assert Reservation.objects.get(pk=reservation["id"]).status == "EXPIRED"


# --- Idempotency ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_retries_return_the_original_without_extending_it(world):
    start = timezone.now()
    with at(start):
        first = _held(world)
    with at(start + timedelta(minutes=10)):
        retry = _hold(world)
    assert retry.status_code == 200
    assert retry.json()["id"] == first["id"]
    assert retry.json()["expires_at"] == first["expires_at"]
    assert Reservation.objects.count() == 1


@pytest.mark.django_db
def test_request_key_reuse_for_another_stall_is_a_conflict(world):
    _held(world)
    response = _hold(world, offer=world.offers[1])
    assert (response.status_code, response.json()["error"]["code"]) == (409, "request_key_reused")


@pytest.mark.django_db
def test_old_requests_never_revive_a_hold(world):
    start = timezone.now()
    with at(start):
        first = _held(world)
    with at(start + HOLD + timedelta(minutes=1)):
        retry = _hold(world)
        assert (retry.status_code, retry.json()["id"], retry.json()["status"]) == (
            200,
            first["id"],
            "EXPIRED",
        )
    second = _held(world, offer=world.offers[1], key="k2")
    world.client["A"].post(f"{world.url_a}/{second['id']}/release")
    assert _hold(world, offer=world.offers[1], key="k2").json()["status"] == "RELEASED"
    assert Reservation.objects.filter(status="HELD").count() == 0
    assert first["id"] != second["id"]


@pytest.mark.django_db
@pytest.mark.parametrize("key", ["", "has space", "x" * 65, "sneaky/../key"])
def test_request_key_format(world, key):
    body = {"offer_id": world.offers[0].pk, "request_key": key}
    assert world.client["A"].post(world.url_a, body).status_code in (400, 422)


@pytest.mark.django_db
def test_request_keys_are_scoped_to_account_and_business(world):
    _held(world, key="same")
    assert _hold(world, "B", offer=world.offers[1], key="same").status_code == 201


# --- Release ---------------------------------------------------------------------------------


@pytest.mark.django_db
def test_release_is_idempotent(world):
    reservation = _held(world)
    url = f"{world.url_a}/{reservation['id']}/release"
    first = world.client["A"].post(url).json()
    again = world.client["A"].post(url).json()
    assert first["status"] == again["status"] == "RELEASED"
    assert first["released_at"] == again["released_at"]
    row = Reservation.objects.get()
    assert row.released_by == world.vendor_a


@pytest.mark.django_db
def test_releasing_an_expired_hold_leaves_it_expired(world):
    start = timezone.now()
    with at(start):
        reservation = _held(world)
    with at(start + HOLD):
        data = world.client["A"].post(f"{world.url_a}/{reservation['id']}/release").json()
    assert data["status"] == "EXPIRED"


@pytest.mark.django_db
def test_confirmed_reservations_cannot_be_released(world):
    reservation = _held(world)
    services.confirm_hold(reservation["id"])
    response = world.client["A"].post(f"{world.url_a}/{reservation['id']}/release")
    assert (response.status_code, response.json()["error"]["code"]) == (
        409,
        "reservation_confirmed",
    )


# --- Confirmation (Phase 13 contract) -----------------------------------------------------


@pytest.mark.django_db
def test_confirm_a_live_hold(world):
    reservation = _held(world)
    confirmed = services.confirm_hold(reservation["id"])
    assert confirmed.status == "CONFIRMED" and confirmed.confirmed_at is not None
    # Confirmed stalls stay occupied, even after the hold time.
    with at(confirmed.expires_at + timedelta(hours=1)):
        assert _hold(world, "B", key="b1").json()["error"]["code"] == "stall_unavailable"


@pytest.mark.django_db
def test_confirm_refuses_expired_and_inactive_holds(world):
    start = timezone.now()
    with at(start):
        reservation = _held(world)
    with at(start + HOLD):
        with pytest.raises(Exception) as excinfo:
            services.confirm_hold(reservation["id"])
    assert excinfo.value.code == "hold_expired"
    # The lapse was recorded even though confirmation failed.
    assert Reservation.objects.get().status == "EXPIRED"
    other = _held(world, offer=world.offers[1], key="k2")
    world.client["A"].post(f"{world.url_a}/{other['id']}/release")
    with pytest.raises(Exception) as excinfo:
        services.confirm_hold(other["id"])
    assert excinfo.value.code == "hold_not_active"


# --- Database guarantees ----------------------------------------------------------------------


def _row(world, **overrides):
    now = timezone.now()
    values = dict(
        offer=world.offers[0],
        occurrence=world.occurrence,
        application=world.app_a,
        vendor_business=world.business_a,
        held_by=world.vendor_a,
        price_minor=2500,
        currency="USD",
        request_key="db",
        created_at=now,
        expires_at=now + HOLD,
    )
    return Reservation(**(values | overrides))


@pytest.mark.django_db
def test_database_allows_one_occupant_per_offer_and_per_business(world):
    _row(world).save()
    with pytest.raises(IntegrityError), transaction.atomic():
        _row(
            world, vendor_business=world.business_b, application=world.app_b, request_key="x"
        ).save()
    with pytest.raises(IntegrityError), transaction.atomic():
        _row(world, offer=world.offers[1], request_key="y").save()
    # Ended reservations don't occupy anything.
    Reservation.objects.update(
        status="RELEASED", released_at=timezone.now(), released_by=world.vendor_a
    )
    _row(world, request_key="z").save()


@pytest.mark.django_db
def test_database_rejects_mismatched_offer_or_application(world):
    with pytest.raises(IntegrityError), transaction.atomic():
        _row(world, offer=world.other_offers[0]).save()  # offer of another date
    with pytest.raises(IntegrityError), transaction.atomic():
        _row(world, application=world.app_b).save()  # another business's application
    other_app = Application.objects.create(
        occurrence=world.other_date,
        vendor_business=world.business_a,
        submitted_by=world.vendor_a,
        questions_version=1,
        questions=[],
        answers={},
        vendor_snapshot={},
        submitted_at=timezone.now(),
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        _row(world, application=other_app).save()  # application for another date


@pytest.mark.django_db
def test_database_checks_lifecycle_fields_price_and_currency(world):
    for bad in (
        {"status": "RELEASED"},
        {"status": "CONFIRMED"},
        {"status": "EXPIRED"},
        {"price_minor": -1},
        {"currency": "XYZ"},
        {"request_key": ""},
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            _row(world, **bad).save()
    with pytest.raises(IntegrityError), transaction.atomic():
        now = timezone.now()
        _row(world, created_at=now, expires_at=now).save()


# --- Protecting layouts and prices ------------------------------------------------------------


@pytest.mark.django_db
def test_held_offers_block_version_switch_and_withdrawal(world):
    _held(world)
    layout_services.unpublish_date_layout(
        world.owner, world.org.pk, world.market.pk, world.occurrence.pk
    )
    copy, _ = layout_services.create_version(
        world.owner,
        world.org.pk,
        world.market.pk,
        canvas_width=0,
        canvas_height=0,
        stalls=[],
        copy_of=world.version.pk,
    )
    save = lambda payload: layout_services.save_date_layout(  # noqa: E731
        world.owner, world.org.pk, world.market.pk, world.occurrence.pk, **payload
    )
    with pytest.raises(Exception) as excinfo:
        save(_layout_payload(world, copy) | {"expected_revision": _rev(world)})
    assert excinfo.value.code == "layout_in_use"
    with pytest.raises(Exception) as excinfo:
        save(
            _layout_payload(world, world.version, disable={world.offers[0].stall_id})
            | {"expected_revision": _rev(world)}
        )
    assert excinfo.value.code == "offer_reserved"
    # Other stalls can still be withdrawn, and prices changed.
    save(
        _layout_payload(world, world.version, price=4000, disable={world.offers[1].stall_id})
        | {"expected_revision": _rev(world)}
    )


@pytest.mark.django_db
def test_protection_ends_when_the_hold_does(world):
    start = timezone.now()
    with at(start):
        _held(world)
    layout_services.unpublish_date_layout(
        world.owner, world.org.pk, world.market.pk, world.occurrence.pk
    )
    copy, _ = layout_services.create_version(
        world.owner,
        world.org.pk,
        world.market.pk,
        canvas_width=0,
        canvas_height=0,
        stalls=[],
        copy_of=world.version.pk,
    )
    with at(start + HOLD):
        layout_services.save_date_layout(
            world.owner,
            world.org.pk,
            world.market.pk,
            world.occurrence.pk,
            **_layout_payload(world, copy) | {"expected_revision": _rev(world)},
        )


@pytest.mark.django_db
def test_confirmed_reservations_protect_their_offer(world):
    reservation = _held(world)
    services.confirm_hold(reservation["id"])
    layout_services.unpublish_date_layout(
        world.owner, world.org.pk, world.market.pk, world.occurrence.pk
    )
    with pytest.raises(Exception) as excinfo:
        layout_services.save_date_layout(
            world.owner,
            world.org.pk,
            world.market.pk,
            world.occurrence.pk,
            **_layout_payload(world, world.version, disable={world.offers[0].stall_id})
            | {"expected_revision": _rev(world)},
        )
    assert excinfo.value.code == "offer_reserved"


# --- Public availability ---------------------------------------------------------------------


@pytest.mark.django_db
def test_public_availability_and_privacy(api, world):
    start = timezone.now()
    with at(start):
        reservation = _held(world)
        data = api.get(world.public).json()
    assert data["items"][0] == {
        "stall_id": world.offers[0].stall_id,
        "offer_id": world.offers[0].pk,
        "status": "unavailable",
    }
    assert [i["status"] for i in data["items"]] == ["unavailable", "available", "available"]
    assert set(data) == {"occurrence_id", "items"}
    assert all(set(item) == {"stall_id", "offer_id", "status"} for item in data["items"])
    text = str(data)
    for secret in ("Bees", "a@example.com", "HELD", "reservation", "vendor"):
        assert secret not in text
    assert reservation["id"] not in [
        v for item in data["items"] for k, v in item.items() if k != "offer_id" and k != "stall_id"
    ]
    with at(start + HOLD):
        assert api.get(world.public).json()["items"][0]["status"] == "available"


@pytest.mark.django_db
def test_public_availability_hides_unpublished_layouts(api, world):
    layout_services.unpublish_date_layout(
        world.owner, world.org.pk, world.market.pk, world.occurrence.pk
    )
    assert api.get(world.public).status_code == 404


@pytest.mark.django_db
def test_public_availability_marks_withdrawn_stalls(api, world):
    layout_services.unpublish_date_layout(
        world.owner, world.org.pk, world.market.pk, world.occurrence.pk
    )
    payload = _layout_payload(world, world.version, disable={world.offers[2].stall_id}) | {
        "expected_revision": _rev(world)
    }
    layout_services.save_date_layout(
        world.owner, world.org.pk, world.market.pk, world.occurrence.pk, **payload
    )
    layout_services.publish_date_layout(
        world.owner,
        world.org.pk,
        world.market.pk,
        world.occurrence.pk,
        expected_revision=_rev(world),
    )
    assert api.get(world.public).json()["items"][2] == {
        "stall_id": world.offers[2].stall_id,
        "offer_id": None,
        "status": "not_offered",
    }


# --- Housekeeping -------------------------------------------------------------------------------


@pytest.mark.django_db
def test_expire_holds_command_is_idempotent(world):
    start = timezone.now()
    with at(start):
        _held(world)
        _held(world, "B", offer=world.offers[1], key="b1")
    with at(start + HOLD):
        call_command("expire_holds")
        call_command("expire_holds")
    assert set(Reservation.objects.values_list("status", flat=True)) == {ReservationStatus.EXPIRED}


@pytest.mark.django_db
def test_cancelling_the_date_keeps_reservations(world):
    reservation = _held(world)
    services.confirm_hold(reservation["id"])
    market_services.cancel_occurrence(
        world.owner, world.org.pk, world.market.pk, world.occurrence.pk
    )
    assert Reservation.objects.get().status == "CONFIRMED"
    assert EventOccurrence.objects.get(pk=world.occurrence.pk).status == "CANCELLED"
