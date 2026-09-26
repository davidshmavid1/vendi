from unittest import mock

import pytest
from django.db import OperationalError

LIVE = "/api/v1/health/live"
READY = "/api/v1/health/ready"


def test_liveness_ok_without_database(client):
    # No ``db`` marker: pytest-django raises if this request touches the database.
    response = client.get(LIVE)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_readiness_ok_with_database(client):
    response = client.get(READY)

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok"}}


def test_readiness_returns_safe_503_when_database_unavailable(client, caplog):
    secret_message = (
        'connection to server at "db.internal.example" (10.0.0.5), port 5432 failed: '
        'FATAL: password authentication failed for user "vendi_admin"'
    )
    with mock.patch("core.api.connection.cursor", side_effect=OperationalError(secret_message)):
        response = client.get(READY)

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "checks": {"database": "unavailable"}}
    for leaked in ("db.internal.example", "10.0.0.5", "vendi_admin", "password"):
        assert leaked not in response.content.decode()
        assert leaked not in caplog.text
    assert "OperationalError" in caplog.text


def test_health_responses_are_json(client):
    response = client.get(LIVE)

    assert response["Content-Type"] == "application/json; charset=utf-8"
