from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from core.exceptions import PermissionDenied
from moderation import policy, services
from moderation.models import OrganizationRestriction
from moderation.policy import ParticipationRestricted, ensure_can_participate
from organizations import services as org_services
from organizations.models import OrganizationAuditEvent, OrganizationMembership, Role
from tests.conftest import PASSWORD
from vendors import services as vendor_services
from vendors.models import VendorMembership, VendorRole

REASON = "Repeated no-shows at the Saturday market."


@pytest.fixture
def world(make_user, as_user):
    """Organization A (owner, admin, staff), organization B, and two vendor
    members who share one business."""
    owner = make_user("owner@example.com")
    admin = make_user("admin@example.com")
    staff = make_user("staff@example.com")
    outsider = make_user("outsider@example.com")
    alice = make_user("alice@example.com")
    bob = make_user("bob@example.com")
    org = org_services.create_organization(owner, name="Riverside").organization
    other = org_services.create_organization(outsider, name="Hilltop").organization
    OrganizationMembership.objects.create(organization=org, user=admin, role=Role.ADMIN)
    OrganizationMembership.objects.create(organization=org, user=staff, role=Role.STAFF)
    farm = vendor_services.create_business(
        alice, name="Sunny Acres", category="PRODUCE", contact_email="hi@sunny.example"
    ).business
    VendorMembership.objects.create(business=farm, user=bob, role=VendorRole.MEMBER)
    return SimpleNamespace(
        org=org,
        other=other,
        owner=owner,
        admin=admin,
        staff=staff,
        outsider=outsider,
        alice=alice,
        bob=bob,
        farm=farm,
        client={
            "OWNER": as_user(owner),
            "ADMIN": as_user(admin),
            "STAFF": as_user(staff),
            "OUTSIDER": as_user(outsider),
            "ALICE": as_user(alice),
        },
        url=f"/organizations/{org.pk}/restrictions",
    )


def _restrict(world, **target):
    return services.create_restriction(world.owner, world.org.pk, reason=REASON, **target)


def _can(world, account, business=None, org=None):
    try:
        ensure_can_participate((org or world.org).pk, account=account, vendor_business=business)
    except ParticipationRestricted:
        return False
    return True


# --- Permissions and isolation -------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("role", "expected"),
    [("OWNER", 201), ("ADMIN", 201), ("STAFF", 403), ("OUTSIDER", 404), ("ALICE", 404)],
)
def test_only_owner_and_admin_can_create(world, role, expected):
    response = world.client[role].post(world.url, {"account_id": world.alice.pk, "reason": REASON})

    assert response.status_code == expected
    assert OrganizationRestriction.objects.count() == (1 if expected == 201 else 0)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("role", "expected"), [("OWNER", 200), ("ADMIN", 200), ("STAFF", 403), ("OUTSIDER", 404)]
)
def test_only_owner_and_admin_can_read_and_revoke(world, role, expected):
    restriction = _restrict(world, account_id=world.alice.pk)
    client = world.client[role]

    listed = client.get(world.url)
    fetched = client.get(f"{world.url}/{restriction.pk}")
    revoked = client.post(f"{world.url}/{restriction.pk}/revoke", {"note": "Resolved"})

    assert (listed.status_code, fetched.status_code, revoked.status_code) == (expected,) * 3
    if expected != 200:
        body = listed.content + fetched.content + revoked.content
        assert REASON.encode() not in body
        restriction.refresh_from_db()
        assert restriction.revoked_at is None


@pytest.mark.django_db
def test_anonymous_and_csrf(world, api):
    assert api.get(world.url).status_code == 401
    missing_csrf = world.client["OWNER"].post(
        world.url, {"account_id": world.alice.pk, "reason": REASON}, csrf=False
    )

    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error"]["code"] == "csrf_failed"
    assert not OrganizationRestriction.objects.exists()


@pytest.mark.django_db
def test_restrictions_are_scoped_to_their_organization(world):
    foreign = services.create_restriction(
        world.outsider, world.other.pk, account_id=world.alice.pk, reason="Other org's reason"
    )
    owner = world.client["OWNER"]

    fetched = owner.get(f"{world.url}/{foreign.pk}")
    revoked = owner.post(f"{world.url}/{foreign.pk}/revoke")
    listed = owner.get(world.url).json()["items"]

    assert fetched.status_code == revoked.status_code == 404
    assert listed == []
    foreign.refresh_from_db()
    assert foreign.revoked_at is None
    assert b"Other org" not in fetched.content


# --- Validation and constraints -------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"reason": REASON}, 400, "target_invalid"),
        ({"account_id": 1, "vendor_business_id": 1, "reason": REASON}, 400, "target_invalid"),
        ({"account_id": 999999, "reason": REASON}, 400, "target_not_found"),
        ({"vendor_business_id": 999999, "reason": REASON}, 400, "target_not_found"),
        ({"account_id": "ME", "reason": "   "}, 400, "reason_invalid"),
        ({"account_id": "ME", "reason": "x" * 1001}, 422, "validation_error"),
        (
            {"account_id": "ME", "reason": REASON, "expires_at": "2020-01-01T00:00:00Z"},
            400,
            "expires_at_invalid",
        ),
        (
            {"account_id": "ME", "reason": REASON, "expires_at": "2099-01-01T00:00:00"},
            400,
            "expires_at_invalid",
        ),
        ({"account_id": "ME", "reason": REASON, "created_by": 1}, 422, "validation_error"),
        ({"account_id": "ME", "reason": REASON, "organization_id": 1}, 422, "validation_error"),
    ],
)
def test_create_validation(world, body, status, code):
    body = {k: (world.alice.pk if v == "ME" else v) for k, v in body.items()}

    response = world.client["OWNER"].post(world.url, body)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert not OrganizationRestriction.objects.exists()


@pytest.mark.django_db
def test_database_requires_exactly_one_target_and_real_foreign_keys(world):
    base = {"organization": world.org, "reason": REASON, "created_by": world.owner}
    with pytest.raises(IntegrityError), transaction.atomic():
        OrganizationRestriction.objects.create(**base)
    with pytest.raises(IntegrityError), transaction.atomic():
        OrganizationRestriction.objects.create(
            **base, account=world.alice, vendor_business=world.farm
        )
    # Foreign keys are DEFERRABLE INITIALLY DEFERRED (Django's default on
    # PostgreSQL), so force the check instead of waiting for COMMIT.
    with pytest.raises(IntegrityError), transaction.atomic():
        OrganizationRestriction.objects.create(
            organization=world.org, reason=REASON, created_by=world.owner, account_id=999999
        )
        connection.check_constraints()
    with pytest.raises(IntegrityError), transaction.atomic():
        OrganizationRestriction.objects.create(
            **base, account=world.alice, revoked_at=timezone.now()
        )


# --- Participation policy ---------------------------------------------------------------------


@pytest.mark.django_db
def test_account_restriction_applies_only_to_that_account(world):
    _restrict(world, account_id=world.alice.pk)

    assert not _can(world, world.alice)
    assert not _can(world, world.alice, world.farm)  # alice can't act, even for her business
    assert _can(world, world.bob, world.farm)  # the business and her colleague are unaffected
    assert _can(world, world.bob)


@pytest.mark.django_db
def test_vendor_restriction_applies_to_every_acting_member(world):
    _restrict(world, vendor_business_id=world.farm.pk)

    assert not _can(world, world.alice, world.farm)
    assert not _can(world, world.bob, world.farm)
    assert _can(world, world.alice)  # personal participation, not for the business
    assert _can(world, world.bob)


@pytest.mark.django_db
def test_restriction_has_no_effect_in_other_organizations(world):
    _restrict(world, account_id=world.alice.pk)
    _restrict(world, vendor_business_id=world.farm.pk)

    assert _can(world, world.alice, world.farm, org=world.other)


@pytest.mark.django_db
def test_restricted_account_keeps_login_profile_and_memberships(world, api, mailoutbox):
    _restrict(world, account_id=world.alice.pk)
    _restrict(world, vendor_business_id=world.farm.pk)

    login = api.login("alice@example.com", PASSWORD)
    business = api.get(f"/vendors/{world.farm.pk}")
    world.alice.refresh_from_db()

    assert login.status_code == 200
    assert api.get("/auth/me").status_code == 200
    assert business.status_code == 200
    assert business.json()["business"]["name"] == "Sunny Acres"
    assert world.alice.is_active
    assert world.farm.memberships.count() == 2


@pytest.mark.django_db
def test_restricted_team_member_keeps_team_access(world):
    _restrict(world, account_id=world.staff.pk)

    assert world.client["STAFF"].get(f"/organizations/{world.org.pk}").status_code == 200
    assert not _can(world, world.staff)


@pytest.mark.django_db
def test_expiry_takes_effect_at_read_time(world):
    expires = timezone.now() + timedelta(hours=1)
    restriction = services.create_restriction(
        world.owner, world.org.pk, account_id=world.alice.pk, reason=REASON, expires_at=expires
    )
    later = expires + timedelta(seconds=1)

    assert not _can(world, world.alice)
    with mock.patch("moderation.policy.timezone.now", return_value=later):
        assert _can(world, world.alice)
    assert restriction.status(now=later) == "expired"
    assert restriction.status() == "effective"


@pytest.mark.django_db
def test_revocation_restores_eligibility_and_keeps_history(world):
    first = _restrict(world, account_id=world.alice.pk)

    response = world.client["ADMIN"].post(f"{world.url}/{first.pk}/revoke", {"note": "Appeal ok"})
    second = _restrict(world, account_id=world.alice.pk)

    assert response.status_code == 200
    assert response.json()["status"] == "revoked"
    assert not _can(world, world.alice)
    services.revoke_restriction(world.owner, world.org.pk, second.pk)
    assert _can(world, world.alice)
    first.refresh_from_db()
    assert (first.reason, first.created_by, first.revoked_by, first.revocation_note) == (
        REASON,
        world.owner,
        world.admin,
        "Appeal ok",
    )
    assert OrganizationRestriction.objects.count() == 2


@pytest.mark.django_db
def test_combined_account_and_vendor_restrictions(world):
    account_r = _restrict(world, account_id=world.alice.pk)
    vendor_r = _restrict(world, vendor_business_id=world.farm.pk)

    services.revoke_restriction(world.owner, world.org.pk, account_r.pk)
    assert not _can(world, world.alice, world.farm)  # the business is still restricted
    assert _can(world, world.alice)
    services.revoke_restriction(world.owner, world.org.pk, vendor_r.pk)
    assert _can(world, world.alice, world.farm)


@pytest.mark.django_db
def test_participation_error_is_generic(world):
    _restrict(world, vendor_business_id=world.farm.pk)

    with pytest.raises(ParticipationRestricted) as excinfo:
        ensure_can_participate(world.org.pk, account=world.bob, vendor_business=world.farm)

    assert isinstance(excinfo.value, PermissionDenied)
    assert excinfo.value.code == "participation_restricted"
    assert "no-shows" not in excinfo.value.message
    assert "business" not in excinfo.value.message.lower()


# --- Duplicates, repeated revocation, listing -------------------------------------------------


@pytest.mark.django_db
def test_duplicate_creation_is_a_conflict(world):
    first = world.client["OWNER"].post(world.url, {"account_id": world.alice.pk, "reason": REASON})
    second = world.client["ADMIN"].post(world.url, {"account_id": world.alice.pk, "reason": "x"})

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "already_restricted"
    assert second.json()["error"]["details"] == [{"restriction_id": first.json()["id"]}]
    assert OrganizationRestriction.objects.count() == 1


@pytest.mark.django_db
def test_expired_restriction_does_not_block_a_new_one(world):
    old = _restrict(world, account_id=world.alice.pk)
    OrganizationRestriction.objects.filter(pk=old.pk).update(
        created_at=timezone.now() - timedelta(days=2), expires_at=timezone.now() - timedelta(days=1)
    )

    response = world.client["OWNER"].post(
        world.url, {"account_id": world.alice.pk, "reason": REASON}
    )
    repeat_expired = world.client["OWNER"].post(f"{world.url}/{old.pk}/revoke")

    assert response.status_code == 201
    assert repeat_expired.status_code == 409
    assert repeat_expired.json()["error"]["code"] == "restriction_expired"


@pytest.mark.django_db
def test_repeated_revocation_keeps_original_metadata(world):
    restriction = _restrict(world, account_id=world.alice.pk)
    world.client["OWNER"].post(f"{world.url}/{restriction.pk}/revoke", {"note": "first"})
    restriction.refresh_from_db()
    original = (restriction.revoked_at, restriction.revoked_by_id, restriction.revocation_note)

    again = world.client["ADMIN"].post(f"{world.url}/{restriction.pk}/revoke", {"note": "second"})

    assert again.status_code == 409
    assert again.json()["error"]["code"] == "already_revoked"
    restriction.refresh_from_db()
    assert (
        restriction.revoked_at,
        restriction.revoked_by_id,
        restriction.revocation_note,
    ) == original


@pytest.mark.django_db
def test_list_filters_and_pagination(world):
    effective = _restrict(world, account_id=world.alice.pk)
    revoked = _restrict(world, vendor_business_id=world.farm.pk)
    services.revoke_restriction(world.owner, world.org.pk, revoked.pk)
    expired = _restrict(world, account_id=world.bob.pk)
    OrganizationRestriction.objects.filter(pk=expired.pk).update(
        created_at=timezone.now() - timedelta(days=2), expires_at=timezone.now() - timedelta(days=1)
    )
    owner = world.client["OWNER"]

    def ids(query):
        return [r["id"] for r in owner.get(f"{world.url}{query}").json()["items"]]

    assert ids("?status=effective") == [effective.pk]
    assert ids("?status=revoked") == [revoked.pk]
    assert ids("?status=expired") == [expired.pk]
    assert ids("?target_type=vendor_business") == [revoked.pk]
    assert ids("?status=effective&target_type=vendor_business") == []
    first_page = owner.get(f"{world.url}?limit=2").json()
    assert len(first_page["items"]) == 2 and first_page["next_cursor"]
    assert owner.get(f"{world.url}?status=bogus").status_code == 422


@pytest.mark.django_db
def test_audit_events_record_ids_not_reasons(world):
    restriction = _restrict(world, account_id=world.alice.pk)
    services.revoke_restriction(world.admin, world.org.pk, restriction.pk, note="private note")

    events = OrganizationAuditEvent.objects.filter(subject_type="organizationrestriction")

    assert sorted(events.values_list("action", flat=True)) == [
        "restriction.created",
        "restriction.revoked",
    ]
    for event in events:
        assert "no-shows" not in str(event.details) and "private" not in str(event.details)


@pytest.mark.django_db
def test_policy_check_is_a_plain_function_of_current_state(world):
    assert policy.is_restricted(world.org.pk, account=world.alice) is False
