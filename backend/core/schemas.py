from typing import Any, Literal

from ninja import Schema
from pydantic import ConfigDict

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


class InputSchema(Schema):
    """Base for request bodies: unknown fields are rejected (422), so clients
    cannot slip in internal fields such as is_staff or owner ids."""

    model_config = ConfigDict(extra="forbid")
