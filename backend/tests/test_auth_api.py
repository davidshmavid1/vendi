import smtplib
import threading
import time
from unittest import mock
from urllib.parse import unquote

import pytest
from django.contrib.sessions.models import Session
from django.db import connection

from accounts import services
from accounts.models import User
from core.exceptions import Conflict
from tests.conftest import PASSWORD, link_params

NEW_PASSWORD = "another-long-passphrase-42"
INTERNAL_FIELDS = ("password", "is_staff", "is_superuser", "is_active", "last_login", "groups")


def _token_from(mail):
    return unquote(link_params(mail.body)["token"])


def _assert_no_internal_fields(body: dict):
    text = str(body)
    for field in INTERNAL_FIELDS:
        assert f"'{field}'" not in text


# --- Registration -----------------------------------------------------------


@pytest.mark.django_db
def test_register_creates_unverified_independent_account_without_session(api, mailoutbox):
    response = api.post("/auth/register", {"email": " New@Example.com ", "password": PASSWORD})

    assert response.status_code == 201
    body = response.json()
    assert body["account"]["email"] == "new@example.com"
    assert body["account"]["email_verified"] is False
    assert body["verification_email_sent"] is True
    _assert_no_internal_fields(body)

    user = User.objects.get(email="new@example.com")
    assert user.check_password(PASSWORD) and user.password != PASSWORD
    assert not user.is_staff and not user.is_superuser
    assert api.get("/auth/me").status_code == 401  # no session before verification

    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == ["new@example.com"]
    assert "http://frontend.test/verify-email?token=" in mailoutbox[0].body


@pytest.mark.django_db
def test_register_duplicate_email_is_conflict_case_insensitively(api, make_user):
    make_user("sam@example.com")

    response = api.post("/auth/register", {"email": "SAM@example.com", "password": PASSWORD})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_taken"


@pytest.mark.django_db
def test_register_rejects_weak_password_with_details(api):
    response = api.post("/auth/register", {"email": "a@example.com", "password": "password"})

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "password_invalid"
    assert error["details"]
    assert not User.objects.exists()


@pytest.mark.django_db
def test_register_rejects_invalid_email(api):
    response = api.post("/auth/register", {"email": "not-an-email", "password": PASSWORD})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "email_invalid"


@pytest.mark.django_db
def test_register_rejects_internal_fields(api):
    response = api.post(
        "/auth/register",
        {"email": "a@example.com", "password": PASSWORD, "is_staff": True, "is_superuser": True},
    )

    assert response.status_code == 422
    assert not User.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_registrations_create_exactly_one_account():
    barrier = threading.Barrier(2)
    results = []

    def attempt():
        try:
            barrier.wait()
            results.append(services.register("race@example.com", PASSWORD).user.pk)
        except Conflict as exc:
            results.append(exc.code)
        finally:
            connection.close()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(map(str, results))[-1] == "email_taken"
    assert User.objects.filter(email="race@example.com").count() == 1


@pytest.mark.django_db
def test_register_survives_email_failure_and_can_resend(api, mailoutbox, caplog):
    with mock.patch("accounts.emails.send_mail", side_effect=smtplib.SMTPException("relay down")):
        response = api.post("/auth/register", {"email": "a@example.com", "password": PASSWORD})

    assert response.status_code == 201
    assert response.json()["verification_email_sent"] is False
    assert User.objects.filter(email="a@example.com", email_verified_at=None).exists()
    assert "Failed to send verification email" in caplog.text

    resend = api.post("/auth/resend-verification", {"email": "a@example.com"})

    assert resend.status_code == 202
    assert len(mailoutbox) == 1
    assert _token_from(mailoutbox[0]) not in caplog.text


# --- Email verification -----------------------------------------------------


@pytest.mark.django_db
def test_verification_token_verifies_once(api, mailoutbox):
    api.post("/auth/register", {"email": "a@example.com", "password": PASSWORD})
    token = _token_from(mailoutbox[0])

    first = api.post("/auth/verify-email", {"token": token})
    reused = api.post("/auth/verify-email", {"token": token})

    assert first.status_code == 200
    assert first.json()["email_verified"] is True
    assert api.get("/auth/me").status_code == 401  # verification does not log in
    assert reused.status_code == 400
    assert reused.json()["error"]["code"] == "verification_token_invalid"


@pytest.mark.django_db
def test_tampered_verification_token_is_rejected(api, mailoutbox):
    api.post("/auth/register", {"email": "a@example.com", "password": PASSWORD})
    token = _token_from(mailoutbox[0])

    response = api.post("/auth/verify-email", {"token": token[:-2] + "xx"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "verification_token_invalid"
    assert not User.objects.get(email="a@example.com").is_email_verified


@pytest.mark.django_db
def test_expired_verification_token_is_rejected(api, mailoutbox, settings):
    api.post("/auth/register", {"email": "a@example.com", "password": PASSWORD})
    token = _token_from(mailoutbox[0])
    later = time.time() + settings.EMAIL_VERIFICATION_MAX_AGE + 60

    with mock.patch("django.core.signing.time.time", return_value=later):
        response = api.post("/auth/verify-email", {"token": token})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "verification_token_expired"


@pytest.mark.django_db
def test_resend_verification_is_enumeration_safe(api, make_user, mailoutbox):
    make_user("verified@example.com", verified=True)

    responses = [
        api.post("/auth/resend-verification", {"email": email})
        for email in ("nobody@example.com", "verified@example.com")
    ]

    assert {r.status_code for r in responses} == {202}
    assert responses[0].json() == responses[1].json()
    assert mailoutbox == []


@pytest.mark.django_db
def test_resend_verification_is_rate_limited_per_email(api, make_user):
    make_user("a@example.com", verified=False)
    statuses = [
        api.post("/auth/resend-verification", {"email": "A@example.com"}).status_code
        for _ in range(4)
    ]

    assert statuses == [202, 202, 202, 429]


# --- Login, /me, logout --------------------------------------------------------


@pytest.mark.django_db
def test_login_starts_rotated_httponly_session(api, make_user):
    make_user("sam@example.com")
    api.get("/auth/csrf")
    api.http.session.save()
    anonymous_key = api.http.session.session_key

    response = api.login("Sam@Example.com")

    assert response.status_code == 200
    assert response.json()["email"] == "sam@example.com"
    _assert_no_internal_fields(response.json())
    cookie = response.cookies["vendi_sessionid"]
    assert cookie["httponly"] and cookie["samesite"] == "Lax"
    assert cookie.value != anonymous_key
    me = api.get("/auth/me")
    assert me.status_code == 200
    assert set(me.json()) == {"id", "email", "email_verified", "date_joined"}


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("email", "password", "active"),
    [
        ("sam@example.com", "wrong-password", True),
        ("nobody@example.com", PASSWORD, True),
        ("sam@example.com", PASSWORD, False),
    ],
    ids=["wrong-password", "unknown-email", "deactivated"],
)
def test_login_failures_are_generic(api, make_user, email, password, active):
    make_user("sam@example.com", active=active)

    response = api.login(email, password)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_credentials"
    assert response.json()["error"]["message"] == "Email or password is incorrect."
    assert api.get("/auth/me").status_code == 401


@pytest.mark.django_db
def test_login_unverified_account_gets_no_session(api, make_user):
    make_user("sam@example.com", verified=False)

    response = api.login("sam@example.com")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "email_not_verified"
    assert api.get("/auth/me").status_code == 401


@pytest.mark.django_db
def test_login_is_rate_limited_per_email_across_ips(api, make_user):
    make_user("sam@example.com")
    statuses = [
        api.post(
            "/auth/login",
            {"email": "sam@example.com", "password": "wrong"},
            REMOTE_ADDR=f"10.0.0.{i}",
        ).status_code
        for i in range(11)
    ]

    assert statuses[:10] == [400] * 10
    assert statuses[10] == 429


@pytest.mark.django_db
def test_login_is_rate_limited_per_ip(api, make_user):
    statuses = [
        api.post("/auth/login", {"email": f"u{i}@example.com", "password": "x"}).status_code
        for i in range(11)
    ]

    assert statuses[10] == 429
    assert len(set(statuses[:10])) == 1 and statuses[0] != 429


@pytest.mark.django_db
def test_rate_limited_response_uses_error_shape_and_retry_after(api):
    for i in range(10):
        api.post("/auth/login", {"email": f"u{i}@example.com", "password": "x"})

    response = api.post("/auth/login", {"email": "x@example.com", "password": "x"})

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "too_many_requests"
    assert int(response["Retry-After"]) > 0


@pytest.mark.django_db
def test_me_requires_authentication(api):
    response = api.get("/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


@pytest.mark.django_db
def test_logout_deletes_session(api, make_user):
    make_user("sam@example.com")
    api.login("sam@example.com")
    session_key = api.http.cookies["vendi_sessionid"].value

    response = api.post("/auth/logout")

    assert response.status_code == 204
    assert not Session.objects.filter(session_key=session_key).exists()
    assert api.get("/auth/me").status_code == 401


@pytest.mark.django_db
def test_deactivated_account_loses_existing_session(api, make_user):
    user = make_user("sam@example.com")
    api.login("sam@example.com")

    User.objects.filter(pk=user.pk).update(is_active=False)

    assert api.get("/auth/me").status_code == 401


# --- CSRF ---------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/auth/register", {"email": "new@example.com", "password": PASSWORD}),
        ("/auth/login", {"email": "sam@example.com", "password": PASSWORD}),
        ("/auth/logout", {}),
        ("/auth/verify-email", {"token": "x"}),
        ("/auth/resend-verification", {"email": "sam@example.com"}),
        ("/auth/password-reset/request", {"email": "sam@example.com"}),
        (
            "/auth/password-reset/confirm",
            {"uid": "x", "token": "x", "new_password": NEW_PASSWORD},
        ),
    ],
)
def test_state_changing_endpoints_reject_missing_csrf_token(api, make_user, mailoutbox, path, body):
    make_user("sam@example.com")
    api.get("/auth/csrf")  # the cookie alone is not enough

    response = api.post(path, body, csrf=False)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"
    assert not User.objects.filter(email="new@example.com").exists()
    assert mailoutbox == []
    assert api.get("/auth/me").status_code == 401


@pytest.mark.django_db
def test_authenticated_endpoint_rejects_missing_csrf_and_keeps_session(api, make_user):
    make_user("sam@example.com")
    api.login("sam@example.com")

    logout = api.post("/auth/logout", csrf=False)
    change = api.post(
        "/auth/password/change",
        {"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        csrf=False,
    )

    assert logout.status_code == 403
    assert change.status_code == 403
    assert change.json()["error"]["code"] == "csrf_failed"
    assert api.get("/auth/me").status_code == 200


@pytest.mark.django_db
def test_csrf_token_from_other_session_is_rejected(api, make_user):
    make_user("sam@example.com")
    foreign_token = type(api)().csrf_token()
    api.get("/auth/csrf")

    response = api.post(
        "/auth/login",
        {"email": "sam@example.com", "password": PASSWORD},
        csrf=False,
        headers={"X-CSRFToken": foreign_token},
    )

    assert response.status_code == 403


def test_csrf_cookie_is_httponly_and_token_comes_from_endpoint(api):
    response = api.http.get("/api/v1/auth/csrf")

    assert response.status_code == 200
    assert response.json()["csrf_token"]
    cookie = response.cookies["vendi_csrftoken"]
    assert cookie["httponly"] and cookie["samesite"] == "Lax"


# --- Password reset -----------------------------------------------------------


@pytest.mark.django_db
def test_password_reset_request_is_enumeration_safe(api, make_user, mailoutbox):
    make_user("sam@example.com")

    known = api.post("/auth/password-reset/request", {"email": "SAM@example.com"})
    unknown = api.post("/auth/password-reset/request", {"email": "nobody@example.com"})

    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert len(mailoutbox) == 1
    assert "http://frontend.test/reset-password?uid=" in mailoutbox[0].body


@pytest.mark.django_db
def test_password_reset_sets_password_logs_out_everywhere_and_is_single_use(
    api, make_user, mailoutbox
):
    make_user("sam@example.com")
    other_device = type(api)()
    other_device.login("sam@example.com")
    api.post("/auth/password-reset/request", {"email": "sam@example.com"})
    params = link_params(mailoutbox[0].body)
    confirm = {"uid": params["uid"], "token": params["token"], "new_password": NEW_PASSWORD}

    response = api.post("/auth/password-reset/confirm", confirm)
    reused = api.post("/auth/password-reset/confirm", confirm)

    assert response.status_code == 204
    assert api.get("/auth/me").status_code == 401  # not logged in by the reset
    assert other_device.get("/auth/me").status_code == 401  # old sessions invalid
    assert reused.status_code == 400
    assert reused.json()["error"]["code"] == "reset_token_invalid"
    assert api.login("sam@example.com", NEW_PASSWORD).status_code == 200


@pytest.mark.django_db
def test_expired_password_reset_token_is_rejected(api, make_user, mailoutbox, settings):
    make_user("sam@example.com")
    api.post("/auth/password-reset/request", {"email": "sam@example.com"})
    params = link_params(mailoutbox[0].body)
    settings.PASSWORD_RESET_TIMEOUT = -1

    response = api.post(
        "/auth/password-reset/confirm",
        {"uid": params["uid"], "token": params["token"], "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "reset_token_invalid"


@pytest.mark.django_db
def test_password_reset_validates_new_password_and_keeps_token_usable(api, make_user, mailoutbox):
    make_user("sam@example.com")
    api.post("/auth/password-reset/request", {"email": "sam@example.com"})
    params = link_params(mailoutbox[0].body)

    weak = api.post(
        "/auth/password-reset/confirm",
        {"uid": params["uid"], "token": params["token"], "new_password": "12345678"},
    )
    strong = api.post(
        "/auth/password-reset/confirm",
        {"uid": params["uid"], "token": params["token"], "new_password": NEW_PASSWORD},
    )

    assert weak.status_code == 400
    assert weak.json()["error"]["code"] == "password_invalid"
    assert strong.status_code == 204


@pytest.mark.django_db
def test_password_reset_request_is_rate_limited_per_email(api, make_user):
    make_user("sam@example.com")
    statuses = [
        api.post("/auth/password-reset/request", {"email": "sam@example.com"}).status_code
        for _ in range(4)
    ]

    assert statuses == [202, 202, 202, 429]


@pytest.mark.django_db
def test_register_is_rate_limited_per_ip(api):
    statuses = [
        api.post("/auth/register", {"email": f"u{i}@example.com", "password": PASSWORD}).status_code
        for i in range(6)
    ]

    assert statuses == [201] * 5 + [429]


# --- Password change ----------------------------------------------------------


@pytest.mark.django_db
def test_password_change_keeps_current_session_and_ends_others(api, make_user):
    make_user("sam@example.com")
    other_device = type(api)()
    other_device.login("sam@example.com")
    api.login("sam@example.com")

    response = api.post(
        "/auth/password/change", {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )

    assert response.status_code == 204
    assert api.get("/auth/me").status_code == 200
    assert other_device.get("/auth/me").status_code == 401
    assert User.objects.get(email="sam@example.com").check_password(NEW_PASSWORD)


@pytest.mark.django_db
def test_password_change_requires_correct_current_password(api, make_user):
    make_user("sam@example.com")
    api.login("sam@example.com")

    response = api.post(
        "/auth/password/change", {"current_password": "wrong", "new_password": NEW_PASSWORD}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "current_password_incorrect"


@pytest.mark.django_db
def test_password_change_requires_authentication(api):
    response = api.post(
        "/auth/password/change", {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )

    assert response.status_code == 401
