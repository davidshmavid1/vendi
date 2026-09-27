"""Expected failures raised by application operations.

These carry a stable machine-readable ``code`` and a client-safe ``message``
but know nothing about HTTP. The API layer maps each class to a status code
in ``core/errors.py``.
"""


class DomainError(Exception):
    default_code = "invalid_request"

    def __init__(self, message: str, *, code: str | None = None, details: list[dict] | None = None):
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.details = details


class InvalidRequest(DomainError):
    """The request is well-formed but breaks a business rule (400)."""


class NotAuthenticated(DomainError):
    """No verified identity where one is required (401)."""

    default_code = "not_authenticated"


class PermissionDenied(DomainError):
    """The caller is known but not allowed to do this (403)."""

    default_code = "permission_denied"


class NotFound(DomainError):
    """The resource does not exist, or the caller may not know it exists (404)."""

    default_code = "not_found"


class Conflict(DomainError):
    """The request conflicts with current state, e.g. a duplicate (409)."""

    default_code = "conflict"


class ServiceUnavailable(DomainError):
    """A dependency (e.g. the payment provider) couldn't be reached and the
    outcome is unknown; retrying the same request is safe (503)."""

    default_code = "service_unavailable"
