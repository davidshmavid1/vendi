import hashlib
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock
from urllib.parse import unquote

import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from organizations import services
from organizations.models import (
    InvitationStatus,
    Organization,
    OrganizationAuditEvent,
    OrganizationInvitation,
    OrganizationMembership,
    Role,
)
from tests.conftest import link_params


def _token(mail):
    return unquote(link_params(mail.body)["token"])


@pytest.fixture
def team(make_user, as_user):
    """One organization with an owner, an admin and a staff member, plus an
    outsider who owns a different organization."""
    owner = make_user("owner@example.com")
    admin = make_user("admin@example.com")
    staff = make_user("staff@example.com")
    outsider = make_user("outsider@example.com")
    org = services.create_organization(owner, name="Riverside Market").organization
    other = services.create_organization(outsider, name="Hilltop Popups").organization
    admin_m = OrganizationMembership.objects.create(organization=org, user=admin, role=Role.ADMIN)
    staff_m = OrganizationMembership.objects.create(organization=org, user=staff, role=Role.STAFF)
    return SimpleNamespace(
        org=org,
        other=other,
        owner=owner,
        admin=admin,
        staff=staff,
        outsider=outsider,
        owner_m=org.memberships.get(user=owner),
        admin_m=admin_m,
        staff_m=staff_m,
        client={
            "OWNER": as_user(owner),
            "ADMIN": as_user(admin),
            "STAFF": as_user(staff),
            "OUTSIDER": as_user(outsider),
        },
        url=f"/organizations/{org.pk}",
    )


def _owners(org):
    return OrganizationMembership.objects.filter(organization=org, role=Role.OWNER).count()


# --- Creating and reading organizations --------------------------------------


@pytest.mark.django_db
def test_verified_user_creates_organization_as_owner(make_user, as_user):
    user = make_user("sam@example.com")

    response = as_user(user).post("/organizations", {"name": "  Riverside Market "})

    assert response.status_code == 201
    body = response.json()
    assert body["role"] == "OWNER"
    assert body["organization"]["name"] == "Riverside Market"
    assert body["organization"]["slug"] == "riverside-market"
    org = Organization.objects.get(pk=body["organization"]["id"])
    assert org.memberships.get().user == user
    assert OrganizationAuditEvent.objects.filter(
        organization=org, action="organization.created", actor=user
    ).exists()


@pytest.mark.django_db
def test_organization_and_owner_membership_are_created_atomically(make_user):
    user = make_user("sam@example.com")

    with (
        mock.patch("organizations.services._audit", side_effect=RuntimeError("boom")),
        pytest.raises(RuntimeError),
    ):
        services.create_organization(user, name="Riverside")

    assert not Organization.objects.exists()
    assert not OrganizationMembership.objects.exists()


@pytest.mark.django_db
def test_slug_collisions(make_user, as_user):
    client = as_user(make_user("sam@example.com"))
    client.post("/organizations", {"name": "Riverside"})

    auto = client.post("/organizations", {"name": "Riverside"})
    taken = client.post("/organizations", {"name": "Other", "slug": "riverside"})
    invalid = client.post("/organizations", {"name": "Other", "slug": "Bad Slug"})

    assert auto.status_code == 201
    assert auto.json()["organization"]["slug"].startswith("riverside-")
    assert taken.status_code == 409
    assert taken.json()["error"]["code"] == "slug_taken"
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "slug_invalid"


@pytest.mark.django_db
def test_unverified_anonymous_and_deactivated_users_cannot_create(make_user, as_user, api):
    unverified = make_user("new@example.com", verified=False)
    deactivated = make_user("gone@example.com")
    deactivated_client = as_user(deactivated)
    deactivated.is_active = False
    deactivated.save()

    responses = {
        "unverified": as_user(unverified).post("/organizations", {"name": "X"}),
        "anonymous": api.post("/organizations", {"name": "X"}),
        "deactivated": deactivated_client.post("/organizations", {"name": "X"}),
    }

    assert responses["unverified"].status_code == 403
    assert responses["unverified"].json()["error"]["code"] == "email_not_verified"
    assert responses["anonymous"].status_code == 401
    assert responses["deactivated"].status_code == 401
    assert not Organization.objects.exists()


@pytest.mark.django_db
def test_user_belongs_to_several_organizations_with_different_roles(team):
    OrganizationMembership.objects.create(organization=team.other, user=team.owner, role=Role.STAFF)

    response = team.client["OWNER"].get("/organizations")

    roles = {item["organization"]["id"]: item["role"] for item in response.json()["items"]}
    assert roles == {team.org.pk: "OWNER", team.other.pk: "STAFF"}


@pytest.mark.django_db
def test_database_rejects_duplicate_membership_and_second_owner(team):
    with pytest.raises(IntegrityError), transaction.atomic():
        OrganizationMembership.objects.create(
            organization=team.org, user=team.staff, role=Role.STAFF
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        OrganizationMembership.objects.filter(pk=team.admin_m.pk).update(role=Role.OWNER)


@pytest.mark.django_db
def test_deleting_a_member_user_does_not_delete_the_organization(team):
    with pytest.raises(ProtectedError):
        team.owner.delete()

    assert Organization.objects.filter(pk=team.org.pk).exists()
    assert _owners(team.org) == 1


@pytest.mark.django_db
def test_organization_list_is_paginated(make_user, as_user):
    user = make_user("sam@example.com")
    for i in range(3):
        services.create_organization(user, name=f"Market {i}")
    client = as_user(user)

    first = client.get("/organizations?limit=2").json()
    second = client.get(f"/organizations?limit=2&cursor={first['next_cursor']}").json()

    assert len(first["items"]) == 2 and first["next_cursor"] is not None
    assert len(second["items"]) == 1 and second["next_cursor"] is None


# --- Permission matrix -----------------------------------------------------------


def _action(team, name):
    c = team.client
    url = team.url
    return {
        "view": lambda r: c[r].get(url),
        "update": lambda r: c[r].patch(url, {"name": "Renamed"}),
        "list_members": lambda r: c[r].get(f"{url}/members"),
        "list_invitations": lambda r: c[r].get(f"{url}/invitations"),
        "invite_staff": lambda r: c[r].post(
            f"{url}/invitations", {"email": "new@example.com", "role": "STAFF"}
        ),
        "invite_admin": lambda r: c[r].post(
            f"{url}/invitations", {"email": "new@example.com", "role": "ADMIN"}
        ),
        "promote_staff": lambda r: c[r].patch(
            f"{url}/members/{team.staff_m.pk}", {"role": "ADMIN"}
        ),
        "demote_admin": lambda r: c[r].patch(f"{url}/members/{team.admin_m.pk}", {"role": "STAFF"}),
        "remove_staff": lambda r: c[r].delete(f"{url}/members/{team.staff_m.pk}"),
        "remove_admin": lambda r: c[r].delete(f"{url}/members/{team.admin_m.pk}"),
        "remove_owner": lambda r: c[r].delete(f"{url}/members/{team.owner_m.pk}"),
        "transfer": lambda r: c[r].post(
            f"{url}/transfer-ownership", {"membership_id": team.staff_m.pk}
        ),
    }[name]


MATRIX = {
    #                   OWNER ADMIN STAFF OUTSIDER
    "view": (200, 200, 200, 404),
    "update": (200, 200, 403, 404),
    "list_members": (200, 200, 200, 404),
    "list_invitations": (200, 200, 403, 404),
    "invite_staff": (201, 201, 403, 404),
    "invite_admin": (201, 403, 403, 404),
    "promote_staff": (200, 403, 403, 404),
    "demote_admin": (200, 403, 403, 404),
    # 409 where the actor targets their own membership: "use leave instead".
    "remove_staff": (204, 204, 409, 404),
    "remove_admin": (204, 409, 403, 404),
    "remove_owner": (409, 403, 403, 404),
    "transfer": (200, 403, 403, 404),
}


@pytest.mark.django_db
@pytest.mark.parametrize("action", MATRIX)
@pytest.mark.parametrize("role_index", range(4), ids=["OWNER", "ADMIN", "STAFF", "OUTSIDER"])
def test_permission_matrix(team, action, role_index):
    role = ["OWNER", "ADMIN", "STAFF", "OUTSIDER"][role_index]

    response = _action(team, action)(role)

    assert response.status_code == MATRIX[action][role_index], response.content
    assert _owners(team.org) == 1


@pytest.mark.django_db
def test_platform_superuser_gets_no_organization_access(team, make_user, as_user):
    admin = make_user("root@example.com")
    admin.is_staff = admin.is_superuser = True
    admin.save()

    assert as_user(admin).get(team.url).status_code == 404


@pytest.mark.django_db
def test_admin_cannot_promote_self_or_manage_admins(team):
    admin = team.client["ADMIN"]

    promote_self = admin.patch(f"{team.url}/members/{team.admin_m.pk}", {"role": "ADMIN"})
    as_owner = admin.patch(f"{team.url}/members/{team.staff_m.pk}", {"role": "OWNER"})

    assert promote_self.status_code == 403
    assert as_owner.status_code == 422  # OWNER is not an assignable role
    team.admin_m.refresh_from_db()
    assert team.admin_m.role == Role.ADMIN


@pytest.mark.django_db
def test_owner_role_only_changes_through_transfer(team):
    response = team.client["OWNER"].patch(
        f"{team.url}/members/{team.owner_m.pk}", {"role": "STAFF"}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "owner_role_locked"


@pytest.mark.django_db
def test_request_body_cannot_carry_roles_or_actor_ids(team):
    response = team.client["STAFF"].patch(team.url, {"name": "X", "role": "OWNER"})
    invite = team.client["ADMIN"].post(
        f"{team.url}/invitations",
        {"email": "a@example.com", "role": "STAFF", "invited_by": team.owner.pk},
    )

    assert response.status_code == 422
    assert invite.status_code == 422


# --- Tenant isolation ------------------------------------------------------------


@pytest.mark.django_db
def test_ids_from_another_organization_are_not_found(team):
    other_owner_m = team.other.memberships.get()
    other_invite = services.create_invitation(
        team.outsider, team.other.pk, email="x@example.com", role=Role.STAFF
    ).invitation
    owner = team.client["OWNER"]

    responses = [
        owner.get(f"/organizations/{team.other.pk}"),
        owner.get(f"/organizations/{team.other.pk}/members"),
        owner.patch(f"{team.url}/members/{other_owner_m.pk}", {"role": "STAFF"}),
        owner.delete(f"{team.url}/members/{other_owner_m.pk}"),
        owner.post(f"{team.url}/transfer-ownership", {"membership_id": other_owner_m.pk}),
        owner.post(f"{team.url}/invitations/{other_invite.pk}/resend"),
        owner.delete(f"{team.url}/invitations/{other_invite.pk}"),
        owner.get("/organizations/999999"),
    ]

    assert [r.status_code for r in responses] == [404] * len(responses)
    assert all("Hilltop" not in r.content.decode() for r in responses)
    other_invite.refresh_from_db()
    assert other_invite.status == InvitationStatus.PENDING


@pytest.mark.django_db
def test_removed_member_loses_access_on_next_request(team):
    staff = team.client["STAFF"]
    assert staff.get(team.url).status_code == 200

    team.client["OWNER"].delete(f"{team.url}/members/{team.staff_m.pk}")

    assert staff.get(team.url).status_code == 404
    assert staff.get("/auth/me").status_code == 200  # still logged in, just not a member


@pytest.mark.django_db
def test_demoted_admin_loses_admin_rights_on_next_request(team):
    admin = team.client["ADMIN"]
    assert admin.patch(team.url, {"name": "Before"}).status_code == 200

    team.client["OWNER"].patch(f"{team.url}/members/{team.admin_m.pk}", {"role": "STAFF"})

    assert admin.patch(team.url, {"name": "After"}).status_code == 403


# --- Directory fields --------------------------------------------------------------


@pytest.mark.django_db
def test_member_directory_hides_emails_from_staff(team):
    team.staff.first_name, team.staff.last_name = "Sam", "Grower"
    team.staff.save()

    staff_view = team.client["STAFF"].get(f"{team.url}/members").json()["items"]
    owner_view = team.client["OWNER"].get(f"{team.url}/members").json()["items"]

    assert {m["email"] for m in staff_view} == {None}
    assert "staff@example.com" in {m["email"] for m in owner_view}
    assert {"name": "Sam Grower", "role": "STAFF"}.items() <= next(
        m for m in staff_view if m["user_id"] == team.staff.pk
    ).items()
    for member in staff_view:
        assert set(member) == {"membership_id", "user_id", "name", "role", "joined_at", "email"}


# --- Invitations -------------------------------------------------------------------


@pytest.mark.django_db
def test_invitation_lifecycle(team, make_user, as_user, mailoutbox):
    response = team.client["ADMIN"].post(
        f"{team.url}/invitations", {"email": " New@Example.com ", "role": "STAFF"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email_sent"] is True
    assert body["invitation"]["email"] == "new@example.com"
    raw = _token(mailoutbox[0])
    assert "http://frontend.test/invitations/accept?token=" in mailoutbox[0].body
    assert raw not in response.content.decode()
    invitation = OrganizationInvitation.objects.get()
    assert invitation.token_digest == hashlib.sha256(raw.encode()).hexdigest()
    assert not OrganizationMembership.objects.filter(user__email="new@example.com").exists()

    newcomer = as_user(make_user("new@example.com"))
    accepted = newcomer.post("/invitations/accept", {"token": raw})
    reused = newcomer.post("/invitations/accept", {"token": raw})

    assert accepted.status_code == 200
    assert accepted.json()["role"] == "STAFF"
    assert accepted.json()["already_member"] is False
    assert newcomer.get(team.url).status_code == 200
    assert reused.status_code == 400
    assert reused.json()["error"]["code"] == "invitation_invalid"
    assert OrganizationMembership.objects.filter(user__email="new@example.com").count() == 1
    for event in OrganizationAuditEvent.objects.all():
        assert raw not in str(event.details)
        assert "new@example.com" not in str(event.details)


@pytest.mark.django_db
@pytest.mark.parametrize("state", ["expired", "revoked", "tampered"])
def test_unusable_invitation_tokens_are_rejected(team, make_user, as_user, mailoutbox, state):
    services.create_invitation(team.owner, team.org.pk, email="new@example.com", role=Role.STAFF)
    raw = _token(mailoutbox[0])
    if state == "expired":
        OrganizationInvitation.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    elif state == "revoked":
        invitation = OrganizationInvitation.objects.get()
        services.revoke_invitation(team.owner, team.org.pk, invitation.pk)
    else:
        raw = raw[:-3] + "abc"

    response = as_user(make_user("new@example.com")).post("/invitations/accept", {"token": raw})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invitation_invalid"
    assert not OrganizationMembership.objects.filter(user__email="new@example.com").exists()


@pytest.mark.django_db
def test_invitation_requires_matching_verified_account(team, make_user, as_user, mailoutbox):
    services.create_invitation(team.owner, team.org.pk, email="new@example.com", role=Role.STAFF)
    raw = _token(mailoutbox[0])

    wrong = as_user(make_user("someone@example.com")).post("/invitations/accept", {"token": raw})
    unverified = as_user(make_user("new@example.com", verified=False)).post(
        "/invitations/accept", {"token": raw}
    )

    assert wrong.status_code == 403
    assert wrong.json()["error"]["code"] == "invitation_email_mismatch"
    assert "new@example.com" not in wrong.content.decode()
    assert unverified.status_code == 403
    assert unverified.json()["error"]["code"] == "email_not_verified"
    assert OrganizationInvitation.objects.get().status == InvitationStatus.PENDING


@pytest.mark.django_db
def test_accepting_does_not_change_an_existing_members_role(team, as_user):
    # Invite the admin's email as STAFF (possible if it was sent before they joined).
    raw = "known-token"
    OrganizationInvitation.objects.create(
        organization=team.org,
        email="admin@example.com",
        role=Role.STAFF,
        invited_by=team.owner,
        token_digest=hashlib.sha256(raw.encode()).hexdigest(),
        expires_at=timezone.now() + timedelta(days=1),
    )

    response = team.client["ADMIN"].post("/invitations/accept", {"token": raw})

    assert response.status_code == 200
    assert response.json() == {**response.json(), "role": "ADMIN", "already_member": True}
    team.admin_m.refresh_from_db()
    assert team.admin_m.role == Role.ADMIN
    assert (
        OrganizationMembership.objects.filter(organization=team.org, user=team.admin).count() == 1
    )


@pytest.mark.django_db
@pytest.mark.parametrize("change", ["removed", "demoted"])
def test_invitation_dies_when_inviter_loses_authority(team, make_user, as_user, mailoutbox, change):
    services.create_invitation(team.admin, team.org.pk, email="new@example.com", role=Role.STAFF)
    raw = _token(mailoutbox[0])
    if change == "removed":
        services.remove_member(team.owner, team.org.pk, team.admin_m.pk)
    else:
        services.change_role(team.owner, team.org.pk, team.admin_m.pk, role=Role.STAFF)

    response = as_user(make_user("new@example.com")).post("/invitations/accept", {"token": raw})

    assert response.status_code == 400
    assert not OrganizationMembership.objects.filter(user__email="new@example.com").exists()


@pytest.mark.django_db
def test_duplicate_and_redundant_invitations(team):
    owner = team.client["OWNER"]
    first = owner.post(f"{team.url}/invitations", {"email": "new@example.com", "role": "STAFF"})
    duplicate = owner.post(f"{team.url}/invitations", {"email": "new@example.com", "role": "ADMIN"})
    member = owner.post(f"{team.url}/invitations", {"email": "staff@example.com", "role": "STAFF"})

    OrganizationInvitation.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    replaced = owner.post(f"{team.url}/invitations", {"email": "new@example.com", "role": "STAFF"})

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "invitation_pending"
    assert member.status_code == 409
    assert member.json()["error"]["code"] == "already_member"
    assert replaced.status_code == 201
    statuses = sorted(OrganizationInvitation.objects.values_list("status", flat=True))
    assert statuses == [InvitationStatus.EXPIRED, InvitationStatus.PENDING]


@pytest.mark.django_db
def test_failed_invitation_email_is_recoverable_by_resend(team, make_user, as_user, mailoutbox):
    import smtplib

    with mock.patch("accounts.emails.send_mail", side_effect=smtplib.SMTPException("down")):
        created = team.client["OWNER"].post(
            f"{team.url}/invitations", {"email": "new@example.com", "role": "STAFF"}
        )
    invitation_id = created.json()["invitation"]["id"]
    assert created.json()["email_sent"] is False

    first = team.client["OWNER"].post(f"{team.url}/invitations/{invitation_id}/resend")
    old_raw = _token(mailoutbox[0])
    second = team.client["OWNER"].post(f"{team.url}/invitations/{invitation_id}/resend")
    new_raw = _token(mailoutbox[1])
    newcomer = as_user(make_user("new@example.com"))

    assert first.status_code == second.status_code == 200
    assert old_raw != new_raw
    assert newcomer.post("/invitations/accept", {"token": old_raw}).status_code == 400
    assert newcomer.post("/invitations/accept", {"token": new_raw}).status_code == 200


@pytest.mark.django_db
def test_admin_cannot_resend_or_revoke_admin_invitations(team):
    invitation = services.create_invitation(
        team.owner, team.org.pk, email="new@example.com", role=Role.ADMIN
    ).invitation
    admin = team.client["ADMIN"]

    assert admin.post(f"{team.url}/invitations/{invitation.pk}/resend").status_code == 403
    assert admin.delete(f"{team.url}/invitations/{invitation.pk}").status_code == 403
    assert team.client["OWNER"].delete(f"{team.url}/invitations/{invitation.pk}").status_code == 204


@pytest.mark.django_db
def test_invitation_list_exposes_no_secrets(team):
    services.create_invitation(team.owner, team.org.pk, email="new@example.com", role=Role.STAFF)

    items = team.client["ADMIN"].get(f"{team.url}/invitations").json()["items"]

    assert len(items) == 1
    assert "token" not in str(items) and "digest" not in str(items)


@pytest.mark.django_db
def test_invitation_creation_is_rate_limited_per_user(team):
    owner = team.client["OWNER"]
    statuses = [
        owner.post(
            f"{team.url}/invitations", {"email": f"p{i}@example.com", "role": "STAFF"}
        ).status_code
        for i in range(31)
    ]

    assert statuses[:30] == [201] * 30
    assert statuses[30] == 429


# --- Leaving, removal and ownership ------------------------------------------------


@pytest.mark.django_db
def test_members_can_leave_but_owner_must_transfer_first(team):
    staff_leave = team.client["STAFF"].post(f"{team.url}/leave")
    owner_leave = team.client["OWNER"].post(f"{team.url}/leave")
    owner_self_remove = team.client["OWNER"].delete(f"{team.url}/members/{team.owner_m.pk}")

    assert staff_leave.status_code == 204
    assert team.client["STAFF"].get(team.url).status_code == 404
    assert owner_leave.status_code == 409
    assert owner_leave.json()["error"]["code"] == "owner_must_transfer"
    assert owner_self_remove.status_code == 409
    assert _owners(team.org) == 1


@pytest.mark.django_db
def test_ownership_transfer(team):
    response = team.client["OWNER"].post(
        f"{team.url}/transfer-ownership", {"membership_id": team.staff_m.pk}
    )

    assert response.status_code == 200
    assert response.json()["role"] == "OWNER"
    roles = dict(team.org.memberships.values_list("user__email", "role"))
    assert roles == {
        "owner@example.com": "ADMIN",
        "admin@example.com": "ADMIN",
        "staff@example.com": "OWNER",
    }
    # The former owner has ADMIN rights now, and can leave.
    assert (
        team.client["OWNER"]
        .post(f"{team.url}/transfer-ownership", {"membership_id": team.admin_m.pk})
        .status_code
        == 403
    )
    assert team.client["OWNER"].post(f"{team.url}/leave").status_code == 204
    assert OrganizationAuditEvent.objects.filter(
        action="organization.ownership_transferred", subject_id=team.staff_m.pk
    ).exists()


@pytest.mark.django_db
def test_ownership_cannot_go_to_unverified_member(team):
    team.staff.email_verified_at = None
    team.staff.save()

    response = team.client["OWNER"].post(
        f"{team.url}/transfer-ownership", {"membership_id": team.staff_m.pk}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "transfer_target_ineligible"
    assert team.org.memberships.get(role=Role.OWNER).user == team.owner


# --- CSRF and audit ----------------------------------------------------------------


@pytest.mark.django_db
def test_mutations_require_csrf_token(team):
    owner = team.client["OWNER"]

    responses = [
        owner.post("/organizations", {"name": "X"}, csrf=False),
        owner.patch(team.url, {"name": "X"}, csrf=False),
        owner.delete(f"{team.url}/members/{team.staff_m.pk}", csrf=False),
        owner.post(f"{team.url}/leave", csrf=False),
        owner.post(
            f"{team.url}/invitations", {"email": "a@example.com", "role": "STAFF"}, csrf=False
        ),
        owner.post("/invitations/accept", {"token": "x"}, csrf=False),
    ]

    assert [r.status_code for r in responses] == [403] * len(responses)
    assert {r.json()["error"]["code"] for r in responses} == {"csrf_failed"}
    assert team.org.memberships.count() == 3


@pytest.mark.django_db
def test_audit_record_rolls_back_with_failed_change(team):
    with (
        mock.patch.object(
            OrganizationMembership, "delete", side_effect=RuntimeError("db went away")
        ),
        pytest.raises(RuntimeError),
    ):
        services.remove_member(team.owner, team.org.pk, team.staff_m.pk)

    assert not OrganizationAuditEvent.objects.filter(action="membership.removed").exists()
    assert team.org.memberships.filter(pk=team.staff_m.pk).exists()
