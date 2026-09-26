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
        headers = kwargs.pop("headers", {})
        if csrf:
            headers["X-CSRFToken"] = self.csrf_token()
        return self.http.post(
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
