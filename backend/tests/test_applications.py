from datetime import datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from applications import services
from applications.models import Application, ApplicationEvent, ApplicationStatus
from markets import services as market_services
from moderation import services as moderation_services
from organizations import services as org_services
from organizations.models import OrganizationMembership, Role
from vendors import services as vendor_services
from vendors.models import VendorMembership, VendorRole

CHICAGO = ZoneInfo("America/Chicago")
VENUE = {
    "venue_name": "Riverside Park",
    "address_line1": "100 River Rd",
    "city": "Springfield",
    "country": "US",
}
QUESTIONS = [
    {"id": "products", "type": "short_text", "label": "What do you sell?", "required": True},
    {"id": "story", "type": "long_text", "label": "Tell us more", "required": False},
    {
        "id": "power",
        "type": "single_choice",
        "label": "Need power?",
        "required": True,
        "choices": ["Yes", "No"],
    },
    {"id": "rules", "type": "acknowledgement", "label": "I accept the rules", "required": True},
]
ANSWERS = {"products": "Honey", "power": "No", "rules": True}


def _future(days, hour=8):
    day = timezone.now().astimezone(CHICAGO).date() + timedelta(days=days)
    return datetime.combine(day, time(hour), tzinfo=CHICAGO)


@pytest.fixture
def world(make_user, as_user):
    owner, admin = make_user("owner@example.com"), make_user("admin@example.com")
    staff, outsider = make_user("staff@example.com"), make_user("outsider@example.com")
    vendor, member = make_user("vendor@example.com"), make_user("member@example.com")
    organization = org_services.create_organization(owner, name="Riverside").organization
    other_org = org_services.create_organization(outsider, name="Hilltop").organization
    OrganizationMembership.objects.create(organization=organization, user=admin, role=Role.ADMIN)
    OrganizationMembership.objects.create(organization=organization, user=staff, role=Role.STAFF)
    market = market_services.create_market(
        owner,
        organization.pk,
        name="Saturday Market",
        market_type="FARMERS_MARKET",
        timezone="America/Chicago",
        **VENUE,
    )
    occurrence = market_services.create_occurrence(
        owner, organization.pk, market.pk, starts_at=_future(5), ends_at=_future(5, 12)
    )
    market_services.publish_market(owner, organization.pk, market.pk)
    business = vendor_services.create_business(
        vendor, name="Bee Happy", category="PRODUCE", contact_email="hi@bees.example"
    ).business
    VendorMembership.objects.create(business=business, user=member, role=VendorRole.MEMBER)
    ns = SimpleNamespace(
        org=organization,
        other_org=other_org,
        market=market,
        occurrence=occurrence,
        business=business,
        owner=owner,
        vendor=vendor,
        member=member,
        outsider=outsider,
        client={
            "OWNER": as_user(owner),
            "ADMIN": as_user(admin),
            "STAFF": as_user(staff),
            "OUTSIDER": as_user(outsider),
            "VENDOR": as_user(vendor),
            "MEMBER": as_user(member),
        },
        settings_url=(
            f"/organizations/{organization.pk}/markets/{market.pk}"
            f"/occurrences/{occurrence.pk}/application-settings"
        ),
        apply_url=f"/vendors/{business.pk}/applications",
        review_url=f"/organizations/{organization.pk}/applications",
    )
    return ns


def _open(w, **overrides):
    values = {
        "enabled": True,
        "opens_at": None,
        "closes_at": None,
        "instructions": "Bring a tent.",
        "questions": QUESTIONS,
    } | overrides
    return services.configure_intake(w.owner, w.org.pk, w.market.pk, w.occurrence.pk, **values)[1]


def _submit(w, answers=ANSWERS, client="VENDOR", version=None):
    intake = w.occurrence.application_intake
    intake.refresh_from_db()
    return w.client[client].post(
        w.apply_url,
        {
            "occurrence_id": w.occurrence.pk,
            "questions_version": version or intake.questions_version,
            "answers": answers,
        },
    )


def _submitted(w) -> Application:
    _open(w)
    return services.submit(
        w.vendor,
        w.business.pk,
        occurrence_id=w.occurrence.pk,
        questions_version=1,
        answers=ANSWERS,
    )


# --- Intake configuration -------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("role", "write", "read"),
    [
        ("OWNER", 200, 200),
        ("ADMIN", 200, 200),
        ("STAFF", 403, 200),
        ("OUTSIDER", 404, 404),
        ("VENDOR", 404, 404),
    ],
)
def test_intake_configuration_permissions(world, role, write, read):
    body = {"enabled": True, "instructions": "Hi", "questions": QUESTIONS}
    assert world.client[role].put(world.settings_url, body).status_code == write
    response = world.client[role].get(world.settings_url)
    assert response.status_code == read


@pytest.mark.django_db
def test_intake_of_another_organizations_occurrence_is_not_found(world):
    # The outsider owns Hilltop; the occurrence belongs to Riverside.
    url = world.settings_url.replace(
        f"/organizations/{world.org.pk}/", f"/organizations/{world.other_org.pk}/"
    )
    response = world.client["OUTSIDER"].put(url, {"enabled": True})
    assert response.status_code == 404


@pytest.mark.django_db
def test_intake_settings_round_trip(world):
    body = {
        "enabled": True,
        "opens_at": (timezone.now() - timedelta(days=1)).isoformat(),
        "closes_at": _future(4).isoformat(),
        "instructions": "  Bring a tent.  ",
        "questions": QUESTIONS,
    }
    data = world.client["ADMIN"].put(world.settings_url, body).json()
    assert data["state"] == "open"
    assert data["instructions"] == "Bring a tent."
    assert data["questions"][0] == {
        "id": "products",
        "type": "short_text",
        "label": "What do you sell?",
        "required": True,
        "choices": [],
    }
    assert data["questions_version"] == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("opens", "closes", "code"),
    [
        (2, 1, "window_invalid"),  # closes before opens (days from now)
        (None, 6, "window_invalid"),  # closes after the event starts (day 5)
        (6, None, "window_invalid"),  # opens after the event starts
    ],
)
def test_intake_window_validation(world, opens, closes, code):
    body = {
        "enabled": True,
        "opens_at": _future(opens).isoformat() if opens else None,
        "closes_at": _future(closes, 9).isoformat() if closes else None,
    }
    response = world.client["OWNER"].put(world.settings_url, body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == code


@pytest.mark.django_db
def test_intake_timestamps_need_an_offset(world):
    body = {"enabled": True, "opens_at": "2030-01-01T08:00:00"}
    response = world.client["OWNER"].put(world.settings_url, body)
    assert response.json()["error"]["code"] == "timestamp_invalid"


@pytest.mark.django_db
def test_intake_window_order_is_a_database_constraint(world):
    intake = _open(world)
    intake.opens_at, intake.closes_at = _future(2), _future(1)
    with pytest.raises(IntegrityError), transaction.atomic():
        intake.save()


@pytest.mark.django_db
def test_archived_market_intake_cannot_change(world):
    market_services.archive_market(world.owner, world.org.pk, world.market.pk)
    response = world.client["OWNER"].put(world.settings_url, {"enabled": True})
    assert response.json()["error"]["code"] == "market_archived"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "question",
    [
        {"id": "Bad Id", "type": "short_text", "label": "x"},
        {"id": "a", "type": "short_text", "label": "   "},
        {"id": "a", "type": "single_choice", "label": "x", "choices": ["only"]},
        {"id": "a", "type": "single_choice", "label": "x", "choices": ["same", "same"]},
        {"id": "a", "type": "short_text", "label": "x", "choices": ["a", "b"]},
    ],
)
def test_invalid_questions_are_rejected(world, question):
    response = world.client["OWNER"].put(
        world.settings_url, {"enabled": True, "questions": [question]}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "questions_invalid"


@pytest.mark.django_db
def test_question_limits(world):
    duplicate = [{"id": "a", "type": "short_text", "label": "x"}] * 2
    response = world.client["OWNER"].put(
        world.settings_url, {"enabled": True, "questions": duplicate}
    )
    assert response.json()["error"]["details"][0]["message"] == "id is used by another question."
    too_many = [{"id": f"q{i}", "type": "short_text", "label": "x"} for i in range(21)]
    response = world.client["OWNER"].put(
        world.settings_url, {"enabled": True, "questions": too_many}
    )
    assert response.status_code == 422
    unknown_type = [{"id": "a", "type": "file_upload", "label": "x"}]
    response = world.client["OWNER"].put(
        world.settings_url, {"enabled": True, "questions": unknown_type}
    )
    assert response.status_code == 422


@pytest.mark.django_db
def test_questions_version_changes_only_with_the_questions(world):
    assert _open(world).questions_version == 1
    assert _open(world, instructions="New text", enabled=False).questions_version == 1
    changed = [*QUESTIONS[:1], {**QUESTIONS[1], "label": "Changed"}]
    assert _open(world, questions=changed).questions_version == 2


# --- Public intake --------------------------------------------------------------------


@pytest.mark.django_db
def test_public_intake_shows_questions_only_while_accepting(api, world):
    url = f"/public/occurrences/{world.occurrence.pk}/application"
    data = api.get(url).json()
    assert data["state"] == "not_accepting"
    assert data["questions"] == []
    _open(world)
    data = api.get(url).json()
    assert data["state"] == "open"
    assert [q["id"] for q in data["questions"]] == ["products", "story", "power", "rules"]
    assert data["questions_version"] == 1
    assert "updated_by" not in str(data)
    _open(world, enabled=False)
    data = api.get(url).json()
    assert (data["state"], data["instructions"], data["questions"]) == ("not_accepting", "", [])


@pytest.mark.django_db
def test_public_intake_states(api, world):
    url = f"/public/occurrences/{world.occurrence.pk}/application"
    _open(world, opens_at=_future(1))
    assert api.get(url).json()["state"] == "not_open_yet"
    _open(
        world,
        opens_at=timezone.now() - timedelta(days=2),
        closes_at=timezone.now() - timedelta(days=1),
    )
    assert api.get(url).json()["state"] == "closed"
    windows = api.get(f"/public/markets/{world.market.pk}/application-windows").json()["items"]
    assert windows == [
        {
            "occurrence_id": world.occurrence.pk,
            "state": "closed",
            "opens_at": None,
            "closes_at": None,
        }
    ]


@pytest.mark.django_db
def test_public_intake_of_unpublished_market_is_not_found(api, world):
    draft = market_services.create_market(
        world.owner, world.org.pk, name="Draft", market_type="POPUP", timezone="America/Chicago"
    )
    occurrence = market_services.create_occurrence(
        world.owner, world.org.pk, draft.pk, starts_at=_future(3), ends_at=_future(3, 12)
    )
    assert api.get(f"/public/occurrences/{occurrence.pk}/application").status_code == 404
    market_services.archive_market(world.owner, world.org.pk, world.market.pk)
    assert api.get(f"/public/occurrences/{world.occurrence.pk}/application").status_code == 404


# --- Submission -----------------------------------------------------------------------


@pytest.mark.django_db
def test_owner_submits_and_snapshots_are_recorded(world):
    _open(world)
    response = _submit(world, {**ANSWERS, "products": "  Honey  ", "story": ""})
    assert response.status_code == 201, response.content
    data = response.json()
    assert data["status"] == "SUBMITTED"
    assert data["answers"] == {"products": "Honey", "power": "No", "rules": True}
    assert data["questions_version"] == 1
    assert data["vendor_snapshot"]["name"] == "Bee Happy"
    assert data["organizer_name"] == "Riverside"
    application = Application.objects.get(pk=data["id"])
    assert application.submitted_by == world.vendor
    assert list(application.events.values_list("from_status", "to_status", "actor")) == [
        ("", "SUBMITTED", world.vendor.pk)
    ]


@pytest.mark.django_db
def test_submission_needs_the_business_owner(world, make_user, as_user):
    _open(world)
    assert _submit(world, client="MEMBER").status_code == 403
    assert _submit(world, client="OUTSIDER").status_code == 404
    # Organization roles grant nothing on the vendor side.
    assert _submit(world, client="OWNER").status_code == 404
    assert not Application.objects.exists()


@pytest.mark.django_db
def test_submission_needs_a_verified_account(world, make_user, as_user):
    _open(world)
    world.vendor.email_verified_at = None
    world.vendor.save()
    response = _submit(world)
    assert response.json()["error"]["code"] == "email_not_verified"


@pytest.mark.django_db
def test_submission_needs_authentication_and_csrf(api, world):
    _open(world)
    body = {"occurrence_id": world.occurrence.pk, "questions_version": 1, "answers": ANSWERS}
    assert api.post(world.apply_url, body).status_code == 401
    response = world.client["VENDOR"].post(world.apply_url, body, csrf=False)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"
    assert not Application.objects.exists()


@pytest.mark.django_db
def test_client_cannot_set_status_or_actor(world):
    _open(world)
    body = {
        "occurrence_id": world.occurrence.pk,
        "questions_version": 1,
        "answers": ANSWERS,
        "status": "APPROVED",
    }
    assert world.client["VENDOR"].post(world.apply_url, body).status_code == 422
    body = {"message": "ok", "decided_by": world.vendor.pk}
    assert world.client["OWNER"].post(f"{world.review_url}/1/approve", body).status_code == 422


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("settings", "state"),
    [
        ({"enabled": False}, "not_accepting"),
        ({"opens_at": "future"}, "not_open_yet"),
        ({"closes_at": "past"}, "closed"),
    ],
)
def test_submission_outside_intake_is_rejected(world, settings, state):
    now = timezone.now()
    values = {
        k: (
            now + timedelta(days=1)
            if v == "future"
            else now - timedelta(days=1)
            if v == "past"
            else v
        )
        for k, v in settings.items()
    }
    if "closes_at" in values:
        values["opens_at"] = now - timedelta(days=2)
    _open(world, **values)
    response = _submit(world)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "applications_closed"
    assert response.json()["error"]["details"] == [{"state": state}]


@pytest.mark.django_db
def test_submission_without_intake_is_rejected(world):
    body = {"occurrence_id": world.occurrence.pk, "questions_version": 1, "answers": {}}
    response = world.client["VENDOR"].post(world.apply_url, body)
    assert response.json()["error"]["code"] == "applications_closed"


@pytest.mark.django_db
def test_submission_to_started_or_cancelled_dates_is_rejected(world):
    _open(world)
    market_services.cancel_occurrence(
        world.owner, world.org.pk, world.market.pk, world.occurrence.pk
    )
    assert _submit(world).json()["error"]["details"] == [{"state": "closed"}]


@pytest.mark.django_db
def test_submission_after_event_start_is_rejected(world):
    _open(world)
    # Move the event into the past directly (organizer edits can't do this).
    type(world.occurrence).objects.filter(pk=world.occurrence.pk).update(
        starts_at=timezone.now() - timedelta(hours=1), ends_at=timezone.now() + timedelta(hours=3)
    )
    assert _submit(world).json()["error"]["details"] == [{"state": "closed"}]


@pytest.mark.django_db
def test_submission_to_draft_or_archived_market_is_not_found(world):
    _open(world)
    market_services.archive_market(world.owner, world.org.pk, world.market.pk)
    assert _submit(world).status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("answers", "question_id", "message"),
    [
        ({"power": "No", "rules": True}, "products", "This question is required."),
        ({**ANSWERS, "products": "   "}, "products", "This question is required."),
        ({**ANSWERS, "products": "x" * 201}, "products", "Must be at most 200 characters."),
        ({**ANSWERS, "story": "x" * 2001}, "story", "Must be at most 2000 characters."),
        ({**ANSWERS, "power": "Maybe"}, "power", "Choose one of the listed options."),
        ({**ANSWERS, "rules": False}, "rules", "Please check this box."),
        ({**ANSWERS, "rules": "yes"}, "rules", "Must be true or false."),
        ({**ANSWERS, "products": True}, "products", "Must be text."),
        ({**ANSWERS, "extra": "x"}, "extra", "Unknown question."),
    ],
)
def test_answers_are_validated(world, answers, question_id, message):
    _open(world)
    response = _submit(world, answers)
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "answers_invalid"
    assert {"question_id": question_id, "message": message} in error["details"]
    assert not Application.objects.exists()


@pytest.mark.django_db
def test_stale_questionnaire_is_rejected(world):
    _open(world)
    _open(world, questions=QUESTIONS[:2])
    response = _submit(world, {"products": "Honey"}, version=1)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "questions_changed"


@pytest.mark.django_db
def test_one_application_per_business_and_occurrence(world):
    _open(world)
    first = _submit(world).json()
    response = _submit(world)
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "application_exists"
    assert error["details"] == [{"application_id": first["id"], "status": "SUBMITTED"}]
    # Still a duplicate after withdrawal: no resubmission in this phase.
    world.client["VENDOR"].post(f"{world.apply_url}/{first['id']}/withdraw")
    assert _submit(world).json()["error"]["code"] == "application_exists"


@pytest.mark.django_db
def test_duplicate_is_also_a_database_constraint(world):
    application = _submitted(world)
    with pytest.raises(IntegrityError), transaction.atomic():
        Application.objects.create(
            occurrence=application.occurrence,
            vendor_business=application.vendor_business,
            submitted_by=world.vendor,
            questions_version=1,
            questions=[],
            answers={},
            vendor_snapshot={},
            submitted_at=timezone.now(),
        )


@pytest.mark.django_db
def test_decision_fields_are_database_constrained(world):
    application = _submitted(world)
    with pytest.raises(IntegrityError), transaction.atomic():
        Application.objects.filter(pk=application.pk).update(status=ApplicationStatus.APPROVED)
    with pytest.raises(IntegrityError), transaction.atomic():
        Application.objects.filter(pk=application.pk).update(status=ApplicationStatus.WITHDRAWN)


# --- Snapshots ------------------------------------------------------------------------


@pytest.mark.django_db
def test_snapshots_survive_later_edits(world):
    application = _submitted(world)
    _open(world, questions=[{"id": "new", "type": "short_text", "label": "New?"}])
    vendor_services.update_business(world.vendor, world.business.pk, name="Bee Very Happy")
    data = world.client["STAFF"].get(f"{world.review_url}/{application.pk}").json()
    assert data["questions_version"] == 1
    assert [q["id"] for q in data["questions"]] == ["products", "story", "power", "rules"]
    assert data["answers"] == ANSWERS
    assert data["vendor_snapshot"]["name"] == "Bee Happy"
    # The business itself is the live identity.
    assert data["vendor_business_id"] == world.business.pk


# --- Vendor reads and withdrawal ------------------------------------------------------


@pytest.mark.django_db
def test_vendor_members_read_their_business_applications(world):
    application = _submitted(world)
    for role in ("VENDOR", "MEMBER"):
        page = world.client[role].get(world.apply_url).json()
        assert [a["id"] for a in page["items"]] == [application.pk]
        detail = world.client[role].get(f"{world.apply_url}/{application.pk}")
        assert detail.status_code == 200
    filtered = world.client["VENDOR"].get(f"{world.apply_url}?occurrence_id={world.occurrence.pk}")
    assert len(filtered.json()["items"]) == 1
    assert world.client["VENDOR"].get(f"{world.apply_url}?status=APPROVED").json()["items"] == []
    for role in ("OUTSIDER", "OWNER"):
        assert world.client[role].get(world.apply_url).status_code == 404
        assert world.client[role].get(f"{world.apply_url}/{application.pk}").status_code == 404


@pytest.mark.django_db
def test_withdrawal(world):
    application = _submitted(world)
    url = f"{world.apply_url}/{application.pk}/withdraw"
    assert world.client["MEMBER"].post(url).status_code == 403
    assert world.client["OUTSIDER"].post(url).status_code == 404
    assert world.client["VENDOR"].post(url, csrf=False).status_code == 403
    data = world.client["VENDOR"].post(url).json()
    assert data["status"] == "WITHDRAWN"
    assert data["withdrawn_at"]
    again = world.client["VENDOR"].post(url)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "application_not_submitted"
    # Organizers can no longer decide it.
    approve = world.client["OWNER"].post(f"{world.review_url}/{application.pk}/approve")
    assert approve.json()["error"]["code"] == "application_not_submitted"


# --- Organizer review -----------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("role", "read", "decide"),
    [
        ("OWNER", 200, 200),
        ("ADMIN", 200, 200),
        ("STAFF", 200, 403),
        ("OUTSIDER", 404, 404),
        ("VENDOR", 404, 404),
        ("MEMBER", 404, 404),
    ],
)
def test_review_permissions(world, role, read, decide):
    application = _submitted(world)
    client = world.client[role]
    assert client.get(world.review_url).status_code == read
    assert client.get(f"{world.review_url}/{application.pk}").status_code == read
    response = client.post(f"{world.review_url}/{application.pk}/reject", {"message": "No"})
    assert response.status_code == decide
    application.refresh_from_db()
    assert application.status == ("REJECTED" if decide == 200 else "SUBMITTED")


@pytest.mark.django_db
def test_other_organization_cannot_see_applications(world):
    application = _submitted(world)
    other_url = f"/organizations/{world.other_org.pk}/applications"
    assert world.client["OUTSIDER"].get(other_url).json()["items"] == []
    response = world.client["OUTSIDER"].get(f"{other_url}/{application.pk}")
    assert response.status_code == 404
    response = world.client["OUTSIDER"].post(f"{other_url}/{application.pk}/approve")
    assert response.status_code == 404


@pytest.mark.django_db
def test_organizer_list_filters(world):
    application = _submitted(world)
    base = world.review_url
    items = world.client["STAFF"].get(base).json()["items"]
    assert items[0]["vendor_name"] == "Bee Happy"
    assert items[0]["occurrence"]["market_name"] == "Saturday Market"
    for query, count in [
        ("status=SUBMITTED", 1),
        ("status=APPROVED", 0),
        (f"market_id={world.market.pk}", 1),
        (f"occurrence_id={world.occurrence.pk}", 1),
        (f"occurrence_id={world.occurrence.pk + 999}", 0),
    ]:
        assert len(world.client["STAFF"].get(f"{base}?{query}").json()["items"]) == count
    assert application.pk == items[0]["id"]


@pytest.mark.django_db
def test_approval_and_history(world):
    application = _submitted(world)
    url = f"{world.review_url}/{application.pk}"
    data = world.client["ADMIN"].post(f"{url}/approve", {"message": "  Welcome!  "}).json()
    assert data["status"] == "APPROVED"
    assert data["decision_message"] == "Welcome!"
    assert data["decided_by_user_id"] is not None
    assert [(h["from_status"], h["to_status"]) for h in data["history"]] == [
        ("", "SUBMITTED"),
        ("SUBMITTED", "APPROVED"),
    ]
    # Terminal: no second decision, no withdrawal of an approved application.
    for action in ("approve", "reject"):
        response = world.client["OWNER"].post(f"{url}/{action}")
        assert response.json()["error"]["code"] == "application_not_submitted"
    response = world.client["VENDOR"].post(f"{world.apply_url}/{application.pk}/withdraw")
    assert response.json()["error"]["code"] == "application_not_submitted"
    assert ApplicationEvent.objects.filter(application=application).count() == 2


@pytest.mark.django_db
def test_decisions_need_csrf_and_login(api, world):
    application = _submitted(world)
    url = f"{world.review_url}/{application.pk}/approve"
    assert api.post(url).status_code == 401
    assert world.client["OWNER"].post(url, csrf=False).status_code == 403
    application.refresh_from_db()
    assert application.status == "SUBMITTED"


@pytest.mark.django_db
def test_vendor_view_hides_reviewer_identity(world):
    application = _submitted(world)
    world.client["OWNER"].post(f"{world.review_url}/{application.pk}/reject", {"message": "Full"})
    data = world.client["MEMBER"].get(f"{world.apply_url}/{application.pk}").json()
    assert data["status"] == "REJECTED"
    assert data["decision_message"] == "Full"
    for field in ("decided_by_user_id", "submitted_by_user_id", "history", "organization_id"):
        assert field not in data
    public = world.client["OUTSIDER"].get(f"/public/occurrences/{world.occurrence.pk}/application")
    assert "Bee Happy" not in public.content.decode()


# --- Moderation -----------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("target", ["account", "business"])
def test_restricted_vendor_cannot_apply(world, target):
    _open(world)
    moderation_services.create_restriction(
        world.owner,
        world.org.pk,
        account_id=world.vendor.pk if target == "account" else None,
        vendor_business_id=world.business.pk if target == "business" else None,
        reason="Private reason: unpaid fees",
    )
    response = _submit(world)
    assert response.status_code == 403
    error = response.json()["error"]
    assert error["code"] == "participation_restricted"
    assert "unpaid" not in response.content.decode()
    assert not Application.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("target", ["account", "business"])
def test_restriction_after_submission_blocks_approval_only(world, target):
    application = _submitted(world)
    moderation_services.create_restriction(
        world.owner,
        world.org.pk,
        account_id=world.vendor.pk if target == "account" else None,
        vendor_business_id=world.business.pk if target == "business" else None,
        reason="Reason",
    )
    # Still readable, status unchanged.
    data = world.client["VENDOR"].get(f"{world.apply_url}/{application.pk}").json()
    assert data["status"] == "SUBMITTED"
    url = f"{world.review_url}/{application.pk}"
    response = world.client["OWNER"].post(f"{url}/approve")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "applicant_restricted"
    assert world.client["OWNER"].post(f"{url}/reject").json()["status"] == "REJECTED"


@pytest.mark.django_db
def test_approval_needs_an_upcoming_scheduled_date(world):
    application = _submitted(world)
    market_services.cancel_occurrence(
        world.owner, world.org.pk, world.market.pk, world.occurrence.pk
    )
    url = f"{world.review_url}/{application.pk}"
    assert world.client["OWNER"].post(f"{url}/approve").json()["error"]["code"] == (
        "occurrence_unavailable"
    )
    assert world.client["OWNER"].post(f"{url}/reject").status_code == 200


@pytest.mark.django_db
def test_restricted_organization_member_can_still_read(world):
    # Restrictions don't hide existing records from the vendor.
    application = _submitted(world)
    moderation_services.create_restriction(
        world.owner, world.org.pk, vendor_business_id=world.business.pk, reason="r"
    )
    assert world.client["MEMBER"].get(world.apply_url).json()["items"][0]["id"] == application.pk


@pytest.mark.django_db
def test_demo_seed_refuses_without_debug():
    from django.core.management import CommandError, call_command

    with pytest.raises(CommandError):
        call_command("seed_demo_applications")
