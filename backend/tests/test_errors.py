import json
import logging

from django.test import RequestFactory
from ninja.errors import HttpError, ValidationError

from config.api import api


def _request():
    request = RequestFactory().get("/api/v1/example")
    request.request_id = "test-request-id"
    return request


def test_unexpected_error_returns_generic_500_and_logs_details(caplog):
    exc = RuntimeError("secret detail: postgres://user:hunter2@db/prod")

    with caplog.at_level(logging.ERROR, logger="core.errors"):
        response = api.on_exception(_request(), exc)

    assert response.status_code == 500
    body = response.content.decode()
    assert "hunter2" not in body
    assert "secret detail" not in body
    assert '"code": "internal_error"' in body
    assert '"request_id": "test-request-id"' in body
    # Diagnostics stay server-side.
    assert "Unhandled API error" in caplog.text
    assert caplog.records[0].exc_info is not None


def test_validation_error_keeps_details_for_clients():
    errors = [{"type": "missing", "loc": ["body", "name"], "msg": "Field required"}]

    response = api.on_exception(_request(), ValidationError(errors))

    assert response.status_code == 422
    body = json.loads(response.content)
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["details"] == errors
    assert body["error"]["request_id"] == "test-request-id"


def test_http_error_preserves_status_and_message():
    response = api.on_exception(_request(), HttpError(409, "Space already booked."))

    assert response.status_code == 409
    assert b'"code": "conflict"' in response.content
    assert b'"message": "Space already booked."' in response.content
