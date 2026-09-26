import logging

from core.logging import RequestIDFilter
from core.request_id import current_request_id


def test_request_id_generated_when_absent(client):
    response = client.get("/api/v1/health/live")

    assert len(response["X-Request-ID"]) == 32


def test_valid_incoming_request_id_is_reused(client):
    response = client.get("/api/v1/health/live", headers={"X-Request-ID": "abc-123.def_4"})

    assert response["X-Request-ID"] == "abc-123.def_4"


def test_unsafe_incoming_request_id_is_replaced(client):
    response = client.get("/api/v1/health/live", headers={"X-Request-ID": "bad value\n<script>"})

    assert response["X-Request-ID"] != "bad value\n<script>"
    assert len(response["X-Request-ID"]) == 32


def test_log_records_carry_request_id():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)
    token = current_request_id.set("req-42")
    try:
        RequestIDFilter().filter(record)
    finally:
        current_request_id.reset(token)

    assert record.request_id == "req-42"


def test_django_request_logs_carry_request_id(client, caplog):
    with caplog.at_level(logging.WARNING, logger="django.request"):
        response = client.get("/api/v1/does-not-exist", headers={"X-Request-ID": "trace-404"})

    assert response.status_code == 404
    assert [r.request_id for r in caplog.records if r.name == "django.request"] == ["trace-404"]


def test_request_id_cleared_after_request(client):
    client.get("/api/v1/health/live")

    assert current_request_id.get() is None
