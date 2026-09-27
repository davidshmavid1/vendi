import re

import pytest
from django.test import Client
from django.utils import timezone

from accounts.models import User

PASSWORD = "correct-horse-battery-staple"


class ApiClient:
    """Browser-like client: enforces CSRF like production and sends the token
    it got from /auth/csrf, as the frontend will."""

    def __init__(self):
        self.http = Client(enforce_csrf_checks=True)

    def csrf_token(self) -> str:
        return self.http.get("/api/v1/auth/csrf").json()["csrf_token"]

    def post(self, path, data=None, *, csrf=True, **kwargs):
        return self._send("post", path, data, csrf=csrf, **kwargs)

    def patch(self, path, data=None, *, csrf=True, **kwargs):
        return self._send("patch", path, data, csrf=csrf, **kwargs)

    def delete(self, path, *, csrf=True, **kwargs):
        return self._send("delete", path, None, csrf=csrf, **kwargs)

    def _send(self, method, path, data, *, csrf, **kwargs):
        headers = kwargs.pop("headers", {})
        if csrf:
            headers["X-CSRFToken"] = self.csrf_token()
        return getattr(self.http, method)(
            f"/api/v1{path}", data or {}, content_type="application/json", headers=headers, **kwargs
        )

    def get(self, path, **kwargs):
        return self.http.get(f"/api/v1{path}", **kwargs)

    def login(self, email, password=PASSWORD):
        return self.post("/auth/login", {"email": email, "password": password})


@pytest.fixture
def api():
    return ApiClient()


@pytest.fixture
def make_user(db):
    def _make(email="sam@example.com", *, verified=True, active=True, password=PASSWORD):
        return User.objects.create_user(
            email=email,
            password=password,
            is_active=active,
            email_verified_at=timezone.now() if verified else None,
        )

    return _make


def link_params(body: str) -> dict:
    """Extract query parameters from the link in an email body."""
    query = re.search(r"https?://\S+\?(\S+)", body).group(1)
    return dict(pair.split("=", 1) for pair in query.split("&"))


@pytest.fixture
def as_user():
    """An ApiClient with an existing session for ``user`` (skips the login API)."""

    def _as(user):
        client = ApiClient()
        client.http.force_login(user)
        return client

    return _as
