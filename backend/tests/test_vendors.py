import hashlib
import smtplib
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock
from urllib.parse import unquote

import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from organizations import services as org_services
from tests.conftest import PASSWORD, link_params
from vendors import services
from vendors.models import (
    InvitationStatus,
    VendorBusiness,
    VendorInvitation,
    VendorMembership,
    VendorRole,
)

PROFILE = {
    "name": "Sunny Acres Farm",
    "category": "PRODUCE",
    "contact_email": "Hello@SunnyAcres.example",
}


def _token(mail):
    return unquote(link_params(mail.body)["token"])


def _owners(business):
    return VendorMembership.objects.filter(business=business, role=VendorRole.OWNER).count()


@pytest.fixture
def shop(make_user, as_user):
    """A vendor business with an owner and a member, plus an outsider who owns
    a different business."""
    owner = make_user("owner@example.com")
    member = make_user("member@example.com")
    outsider = make_user("outsider@example.com")
    business = services.create_business(owner, **PROFILE).business
    other = services.create_business(
        outsider, name="Clay Works", category="CRAFTS", contact_email="clay@example.com"
    ).business
    member_m = VendorMembership.objects.create(
        business=business, user=member, role=VendorRole.MEMBER
    )
    return SimpleNamespace(
        business=business,
        other=other,
        owner=owner,
        member=member,
        outsider=outsider,
        owner_m=business.memberships.get(user=owner),
        member_m=member_m,
        client={"OWNER": as_user(owner), "MEMBER": as_user(member), "OUTSIDER": as_user(outsider)},
        url=f"/vendors/{business.pk}",
    )


# --- Creating and reading ---------------------------------------------------------


@pytest.mark.django_db
def test_verified_user_creates_business_as_owner(make_user, as_user):
    user = make_user("sam@example.com")

    response = as_user(user).post(
        "/vendors",
        {
            **PROFILE,
            "description": "Seasonal vegetables.",
            "phone": "+1 (555) 010-2000",
            "website": "https://sunnyacres.example",
            "city": "Springfield",
            "region": "IL",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["role"] == "OWNER"
    assert body["business"]["contact_email"] == "hello@sunnyacres.example"
    assert body["business"]["city"] == "Springfield"
    business = VendorBusiness.objects.get(pk=body["business"]["id"])
    assert business.memberships.get().user == user
    assert user.email == "sam@example.com"  # login email untouched


@pytest.mark.django_db
def test_business_and_owner_membership_are_created_atomically(make_user):
    user = make_user("sam@example.com")

    with (
        mock.patch.object(
            VendorMembership.objects, "create", side_effect=RuntimeError("db went away")
        ),
        pytest.raises(RuntimeError),
    ):
        services.create_business(user, **PROFILE)

    assert not VendorBusiness.objects.exists()


@pytest.mark.django_db
def test_registration_and_joining_organizations_create_no_vendor_business(api, make_user):
    api.post("/auth/register", {"email": "new@example.com", "password": PASSWORD})
    user = make_user("organizer@example.com")
    org_services.create_organization(user, name="Riverside Market")

    assert not VendorBusiness.objects.exists()
    assert not VendorMembership.objects.exists()


@pytest.mark.django_db
def test_one_account_many_businesses_and_one_business_many_accounts(shop):
    services.create_business(
        shop.member, name="Member Bakery", category="BAKED_GOODS", contact_email="b@example.com"
    )

    items = shop.client["MEMBER"].get("/vendors").json()["items"]
    members = shop.client["OWNER"].get(f"{shop.url}/members").json()["items"]

    assert {(i["business"]["name"], i["role"]) for i in items} == {
        ("Sunny Acres Farm", "MEMBER"),
        ("Member Bakery", "OWNER"),
    }
    assert {m["role"] for m in members} == {"OWNER", "MEMBER"}


@pytest.mark.django_db
def test_unverified_anonymous_and_deactivated_users_cannot_create(make_user, as_user, api):
    unverified = make_user("new@example.com", verified=False)
    gone = make_user("gone@example.com")
    gone_client = as_user(gone)
    gone.is_active = False
    gone.save()

    unverified_response = as_user(unverified).post("/vendors", PROFILE)

    assert unverified_response.status_code == 403
    assert unverified_response.json()["error"]["code"] == "email_not_verified"
    assert api.post("/vendors", PROFILE).status_code == 401
    assert gone_client.post("/vendors", PROFILE).status_code == 401
    assert not VendorBusiness.objects.exists()


# --- Validation and protected fields ------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("changes", "status", "code"),
    [
        ({"name": "   "}, 400, "name_invalid"),
        ({"contact_email": "not-an-email"}, 400, "contact_email_invalid"),
        ({"website": "javascript:alert(1)"}, 400, "website_invalid"),
        ({"website": "sunny.example"}, 400, "website_invalid"),
        ({"phone": "call me"}, 400, "phone_invalid"),
        ({"category": "WEAPONS"}, 422, "validation_error"),
        ({"description": "x" * 2001}, 422, "validation_error"),
    ],
)
def test_profile_validation(shop, changes, status, code):
    response = shop.client["OWNER"].patch(shop.url, changes)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code


@pytest.mark.django_db
@pytest.mark.parametrize(
    "field",
    ["id", "owner", "user_id", "membership_id", "role", "created_at", "organization_id"],
)
def test_protected_fields_cannot_be_submitted(shop, field):
    response = shop.client["OWNER"].patch(shop.url, {"name": "New", field: 1})
    created = shop.client["OWNER"].post("/vendors", {**PROFILE, field: 1})

    assert response.status_code == 422
    assert created.status_code == 422
    shop.business.refresh_from_db()
    assert shop.business.name == "Sunny Acres Farm"


@pytest.mark.django_db
def test_owner_updates_only_sent_fields_and_can_clear_optional_ones(shop):
    shop.business.city = "Springfield"
    shop.business.save()

    response = shop.client["OWNER"].patch(shop.url, {"name": "Sunny Acres", "city": ""})

    assert response.status_code == 200
    shop.business.refresh_from_db()
    assert (shop.business.name, shop.business.city) == ("Sunny Acres", "")
    assert shop.business.contact_email == "hello@sunnyacres.example"


# --- Permissions ----------------------------------------------------------------------


def _action(shop, name):
    c, url = shop.client, shop.url
    return {
        "view": lambda r: c[r].get(url),
        "list_members": lambda r: c[r].get(f"{url}/members"),
        "update": lambda r: c[r].patch(url, {"name": "Renamed"}),
        "list_invitations": lambda r: c[r].get(f"{url}/invitations"),
        "invite": lambda r: c[r].post(f"{url}/invitations", {"email": "new@example.com"}),
        "remove_member": lambda r: c[r].delete(f"{url}/members/{shop.member_m.pk}"),
        "remove_owner": lambda r: c[r].delete(f"{url}/members/{shop.owner_m.pk}"),
        "transfer": lambda r: c[r].post(
            f"{url}/transfer-ownership", {"membership_id": shop.member_m.pk}
        ),
    }[name]


MATRIX = {
    #                   OWNER MEMBER OUTSIDER
    "view": (200, 200, 404),
    "list_members": (200, 200, 404),
    "update": (200, 403, 404),
    "list_invitations": (200, 403, 404),
    "invite": (201, 403, 404),
    "remove_member": (204, 403, 404),
    "remove_owner": (409, 403, 404),
    "transfer": (200, 403, 404),
}


@pytest.mark.django_db
@pytest.mark.parametrize("action", MATRIX)
@pytest.mark.parametrize("index", range(3), ids=["OWNER", "MEMBER", "OUTSIDER"])
def test_permission_matrix(shop, action, index):
    role = ["OWNER", "MEMBER", "OUTSIDER"][index]

    response = _action(shop, action)(role)

    assert response.status_code == MATRIX[action][index], response.content
    assert _owners(shop.business) == 1


@pytest.mark.django_db
def test_member_sees_profile_but_not_member_emails(shop):
    profile = shop.client["MEMBER"].get(shop.url).json()
    members = shop.client["MEMBER"].get(f"{shop.url}/members").json()["items"]
    owner_view = shop.client["OWNER"].get(f"{shop.url}/members").json()["items"]

    assert profile["role"] == "MEMBER"
    assert profile["business"]["contact_email"] == "hello@sunnyacres.example"
    assert {m["email"] for m in members} == {None}
    assert "member@example.com" in {m["email"] for m in owner_view}


@pytest.mark.django_db
def test_ids_from_another_business_are_not_found(shop):
    other_owner_m = shop.other.memberships.get()
    invite = services.create_invitation(
        shop.outsider, shop.other.pk, email="x@example.com"
    ).invitation
    owner = shop.client["OWNER"]

    responses = [
        owner.get(f"/vendors/{shop.other.pk}"),
        owner.get(f"/vendors/{shop.other.pk}/members"),
        owner.patch(f"/vendors/{shop.other.pk}", {"name": "Hijack"}),
        owner.delete(f"{shop.url}/members/{other_owner_m.pk}"),
        owner.post(f"{shop.url}/transfer-ownership", {"membership_id": other_owner_m.pk}),
        owner.post(f"{shop.url}/invitations/{invite.pk}/resend"),
        owner.delete(f"{shop.url}/invitations/{invite.pk}"),
        owner.get("/vendors/999999"),
    ]

    assert [r.status_code for r in responses] == [404] * len(responses)
    assert all("Clay" not in r.content.decode() for r in responses)
    shop.other.refresh_from_db()
    assert shop.other.name == "Clay Works"


@pytest.mark.django_db
def test_organizer_and_vendor_roles_do_not_grant_each_other(shop, make_user, as_user):
    org = org_services.create_organization(shop.outsider, name="Hilltop Market").organization
    org_owner = shop.client["OUTSIDER"]

    assert org_owner.get(shop.url).status_code == 404
    assert shop.client["OWNER"].get(f"/organizations/{org.pk}").status_code == 404

    admin = make_user("root@example.com")
    admin.is_staff = admin.is_superuser = True
    admin.save()
    assert as_user(admin).get(shop.url).status_code == 404


@pytest.mark.django_db
def test_removed_member_loses_access_on_next_request(shop):
    member = shop.client["MEMBER"]
    assert member.get(shop.url).status_code == 200

    shop.client["OWNER"].delete(f"{shop.url}/members/{shop.member_m.pk}")

    assert member.get(shop.url).status_code == 404


@pytest.mark.django_db
def test_mutations_require_csrf(shop):
    owner = shop.client["OWNER"]

    responses = [
        owner.post("/vendors", PROFILE, csrf=False),
        owner.patch(shop.url, {"name": "X"}, csrf=False),
        owner.delete(f"{shop.url}/members/{shop.member_m.pk}", csrf=False),
        owner.post(f"{shop.url}/invitations", {"email": "a@example.com"}, csrf=False),
        shop.client["MEMBER"].post(f"{shop.url}/leave", csrf=False),
        owner.post("/vendor-invitations/accept", {"token": "x"}, csrf=False),
    ]

    assert [r.status_code for r in responses] == [403] * len(responses)
    assert {r.json()["error"]["code"] for r in responses} == {"csrf_failed"}
    assert shop.business.memberships.count() == 2


# --- Database constraints --------------------------------------------------------------


@pytest.mark.django_db
def test_database_rejects_duplicate_membership_and_second_owner(shop):
    with pytest.raises(IntegrityError), transaction.atomic():
        VendorMembership.objects.create(
            business=shop.business, user=shop.member, role=VendorRole.MEMBER
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        VendorMembership.objects.filter(pk=shop.member_m.pk).update(role=VendorRole.OWNER)
    with pytest.raises(IntegrityError), transaction.atomic():
        VendorBusiness.objects.filter(pk=shop.business.pk).update(contact_email="Upper@X.com")


@pytest.mark.django_db
def test_deleting_a_member_user_is_blocked(shop):
    with pytest.raises(ProtectedError):
        shop.owner.delete()

    assert _owners(shop.business) == 1


# --- Invitations -------------------------------------------------------------------------


@pytest.mark.django_db
def test_invitation_lifecycle(shop, make_user, as_user, mailoutbox):
    response = shop.client["OWNER"].post(f"{shop.url}/invitations", {"email": " New@Example.com "})

    assert response.status_code == 201
    assert response.json()["email_sent"] is True
    assert response.json()["invitation"]["email"] == "new@example.com"
    raw = _token(mailoutbox[0])
    assert "http://frontend.test/vendor-invitations/accept?token=" in mailoutbox[0].body
    assert raw not in response.content.decode()
    invitation = VendorInvitation.objects.get()
    assert invitation.token_digest == hashlib.sha256(raw.encode()).hexdigest()
    assert not VendorMembership.objects.filter(user__email="new@example.com").exists()

    newcomer = as_user(make_user("new@example.com"))
    accepted = newcomer.post("/vendor-invitations/accept", {"token": raw})
    reused = newcomer.post("/vendor-invitations/accept", {"token": raw})

    assert accepted.status_code == 200
    assert accepted.json()["role"] == "MEMBER"
    assert newcomer.get(shop.url).status_code == 200
    assert reused.status_code == 400
    assert reused.json()["error"]["code"] == "invitation_invalid"
    assert VendorMembership.objects.filter(user__email="new@example.com").count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize("state", ["expired", "revoked", "tampered"])
def test_unusable_tokens_are_rejected(shop, make_user, as_user, mailoutbox, state):
    services.create_invitation(shop.owner, shop.business.pk, email="new@example.com")
    raw = _token(mailoutbox[0])
    if state == "expired":
        VendorInvitation.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    elif state == "revoked":
        services.revoke_invitation(shop.owner, shop.business.pk, VendorInvitation.objects.get().pk)
    else:
        raw = raw[:-3] + "abc"

    response = as_user(make_user("new@example.com")).post(
        "/vendor-invitations/accept", {"token": raw}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invitation_invalid"
    assert not VendorMembership.objects.filter(user__email="new@example.com").exists()


@pytest.mark.django_db
def test_token_alone_does_not_authorize_another_account(shop, make_user, as_user, mailoutbox):
    services.create_invitation(shop.owner, shop.business.pk, email="new@example.com")
    raw = _token(mailoutbox[0])

    wrong = as_user(make_user("someone@example.com")).post(
        "/vendor-invitations/accept", {"token": raw}
    )
    unverified = as_user(make_user("new@example.com", verified=False)).post(
        "/vendor-invitations/accept", {"token": raw}
    )

    assert wrong.status_code == 403
    assert wrong.json()["error"]["code"] == "invitation_email_mismatch"
    assert "new@example.com" not in wrong.content.decode()
    assert unverified.status_code == 403
    assert unverified.json()["error"]["code"] == "email_not_verified"
    assert VendorInvitation.objects.get().status == InvitationStatus.PENDING


@pytest.mark.django_db
def test_existing_member_keeps_role_when_accepting(shop):
    raw = "known-token"
    VendorInvitation.objects.create(
        business=shop.business,
        email="member@example.com",
        invited_by=shop.owner,
        token_digest=hashlib.sha256(raw.encode()).hexdigest(),
        expires_at=timezone.now() + timedelta(days=1),
    )

    response = shop.client["MEMBER"].post("/vendor-invitations/accept", {"token": raw})

    assert response.status_code == 200
    assert response.json()["already_member"] is True
    assert shop.business.memberships.filter(user=shop.member).count() == 1


@pytest.mark.django_db
def test_invitation_dies_when_inviter_is_no_longer_owner(shop, make_user, as_user, mailoutbox):
    services.create_invitation(shop.owner, shop.business.pk, email="new@example.com")
    raw = _token(mailoutbox[0])
    services.transfer_ownership(shop.owner, shop.business.pk, shop.member_m.pk)

    response = as_user(make_user("new@example.com")).post(
        "/vendor-invitations/accept", {"token": raw}
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_duplicate_and_redundant_invitations(shop):
    owner = shop.client["OWNER"]
    first = owner.post(f"{shop.url}/invitations", {"email": "new@example.com"})
    duplicate = owner.post(f"{shop.url}/invitations", {"email": "NEW@example.com"})
    member = owner.post(f"{shop.url}/invitations", {"email": "member@example.com"})
    VendorInvitation.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    replaced = owner.post(f"{shop.url}/invitations", {"email": "new@example.com"})

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "invitation_pending"
    assert member.status_code == 409
    assert member.json()["error"]["code"] == "already_member"
    assert replaced.status_code == 201
    assert sorted(VendorInvitation.objects.values_list("status", flat=True)) == [
        InvitationStatus.EXPIRED,
        InvitationStatus.PENDING,
    ]


@pytest.mark.django_db
def test_failed_email_is_recoverable_by_resend_which_rotates_token(
    shop, make_user, as_user, mailoutbox
):
    with mock.patch("accounts.emails.send_mail", side_effect=smtplib.SMTPException("down")):
        created = shop.client["OWNER"].post(f"{shop.url}/invitations", {"email": "new@example.com"})
    invitation_id = created.json()["invitation"]["id"]
    assert created.json()["email_sent"] is False

    shop.client["OWNER"].post(f"{shop.url}/invitations/{invitation_id}/resend")
    old_raw = _token(mailoutbox[0])
    shop.client["OWNER"].post(f"{shop.url}/invitations/{invitation_id}/resend")
    new_raw = _token(mailoutbox[1])
    newcomer = as_user(make_user("new@example.com"))

    assert newcomer.post("/vendor-invitations/accept", {"token": old_raw}).status_code == 400
    assert newcomer.post("/vendor-invitations/accept", {"token": new_raw}).status_code == 200


@pytest.mark.django_db
def test_invitation_list_exposes_no_secrets(shop):
    services.create_invitation(shop.owner, shop.business.pk, email="new@example.com")

    items = shop.client["OWNER"].get(f"{shop.url}/invitations").json()["items"]

    assert len(items) == 1
    assert "token" not in str(items) and "digest" not in str(items)


# --- Leaving and ownership ----------------------------------------------------------------


@pytest.mark.django_db
def test_member_can_leave_but_owner_must_transfer_first(shop):
    member_leave = shop.client["MEMBER"].post(f"{shop.url}/leave")
    owner_leave = shop.client["OWNER"].post(f"{shop.url}/leave")

    assert member_leave.status_code == 204
    assert shop.client["MEMBER"].get(shop.url).status_code == 404
    assert owner_leave.status_code == 409
    assert owner_leave.json()["error"]["code"] == "owner_must_transfer"
    assert _owners(shop.business) == 1


@pytest.mark.django_db
def test_ownership_transfer(shop):
    response = shop.client["OWNER"].post(
        f"{shop.url}/transfer-ownership", {"membership_id": shop.member_m.pk}
    )

    assert response.status_code == 200
    assert dict(shop.business.memberships.values_list("user__email", "role")) == {
        "owner@example.com": "MEMBER",
        "member@example.com": "OWNER",
    }
    assert shop.client["OWNER"].patch(shop.url, {"name": "X"}).status_code == 403
    assert shop.client["OWNER"].post(f"{shop.url}/leave").status_code == 204


@pytest.mark.django_db
def test_ownership_cannot_go_to_unverified_member_or_self(shop):
    shop.member.email_verified_at = None
    shop.member.save()

    to_unverified = shop.client["OWNER"].post(
        f"{shop.url}/transfer-ownership", {"membership_id": shop.member_m.pk}
    )
    to_self = shop.client["OWNER"].post(
        f"{shop.url}/transfer-ownership", {"membership_id": shop.owner_m.pk}
    )

    assert to_unverified.json()["error"]["code"] == "transfer_target_ineligible"
    assert to_self.json()["error"]["code"] == "already_owner"
    assert shop.business.memberships.get(role=VendorRole.OWNER).user == shop.owner
