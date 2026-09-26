from typing import Any, Literal

from ninja import Schema

CheckStatus = Literal["ok", "unavailable"]


class LivenessOut(Schema):
    status: Literal["ok"]


class ReadinessOut(Schema):
    status: CheckStatus
    checks: dict[str, CheckStatus]


class ErrorBody(Schema):
    code: str
    message: str
    request_id: str | None
    details: list[dict[str, Any]] | None = None


class ErrorOut(Schema):
    """Shape of every error response from the API."""

    error: ErrorBody
