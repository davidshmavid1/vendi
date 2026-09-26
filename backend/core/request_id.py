"""The current request's correlation ID, available to code that has no
``request`` object (e.g. log filters)."""

import re
import uuid
from contextvars import ContextVar

HEADER = "X-Request-ID"

# Accept a caller-supplied ID (e.g. from a proxy) only if it is short and
# plain, so it is safe to echo back and write to logs.
_VALID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

current_request_id: ContextVar[str | None] = ContextVar("current_request_id", default=None)


def resolve(incoming: str | None) -> str:
    if incoming and _VALID.fullmatch(incoming):
        return incoming
    return uuid.uuid4().hex


def clear(**kwargs) -> None:
    """``request_finished`` receiver: stop attributing logs to the last request."""
    current_request_id.set(None)
