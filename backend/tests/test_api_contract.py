"""API-wide conventions: routing, status codes and the JSON error shape."""

import json

import pytest
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory

from config.api import api
from core import exceptions


def _request():
    request = RequestFactory().get("/api/v1/example")
    request.request_id = "rid-1"
    return request


def _error(response):
    return json.loads(response.content)["error"]


def test_unknown_api_path_returns_json_404(client):
    response = client.get("/api/v1/does-not-exist", headers={"X-Request-ID": "rid-404"})

    assert response.status_code == 404
    assert response["Content-Type"] == "application/json"
    assert response.json()["error"] == {
        "code": "not_found",
        "message": "Not found.",
        "request_id": "rid-404",
        "details": None,
    }


def test_wrong_method_returns_json_405_with_allow_header(client):
    response = client.post("/api/v1/health/live")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"
    assert "GET" in response["Allow"]
    assert response["X-Request-ID"]


def test_non_api_paths_keep_django_behavior(client):
    response = client.get("/not-an-api-path")

    assert response.status_code == 404
    assert not response["Content-Type"].startswith("application/json")


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (exceptions.InvalidRequest("Bad."), 400, "invalid_request"),
        (exceptions.NotAuthenticated("Log in."), 401, "not_authenticated"),
        (exceptions.PermissionDenied("No."), 403, "permission_denied"),
        (exceptions.NotFound("Gone."), 404, "not_found"),
        (exceptions.Conflict("Taken.", code="email_taken"), 409, "email_taken"),
    ],
)
def test_domain_errors_map_to_status_and_code(exc, status, code):
    response = api.on_exception(_request(), exc)

    assert response.status_code == status
    error = _error(response)
    assert error["code"] == code
    assert error["message"] == exc.message
    assert error["request_id"] == "rid-1"


def test_domain_error_subclass_inherits_status():
    class SlotTaken(exceptions.Conflict):
        default_code = "slot_taken"

    response = api.on_exception(_request(), SlotTaken("Slot taken."))

    assert response.status_code == 409
    assert _error(response)["code"] == "slot_taken"


def test_django_permission_denied_is_safe_403():
    response = api.on_exception(_request(), PermissionDenied("internal reason"))

    assert response.status_code == 403
    assert b"internal reason" not in response.content


def test_openapi_is_versioned_and_uses_explicit_schemas(client):
    schema = client.get("/api/v1/openapi.json").json()

    assert all(path.startswith("/api/v1/") for path in schema["paths"])
    for path_item in schema["paths"].values():
        for operation in path_item.values():
            for response in operation["responses"].values():
                content = response.get("content", {}).get("application/json", {})
                assert "$ref" in content.get("schema", {"$ref": ""})
