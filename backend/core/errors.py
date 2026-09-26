"""Consistent JSON error responses for the API.

Every error has the shape ``{"error": {"code", "message", "request_id", "details"}}``.
Expected errors keep their status code and message; unexpected ones are logged
with a traceback server-side and return a generic 500 message.
"""

import logging
from http import HTTPStatus

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404, HttpRequest, HttpResponse
from ninja import NinjaAPI
from ninja.errors import AuthenticationError, HttpError, ValidationError

from core import exceptions

logger = logging.getLogger(__name__)

# HTTP status for each expected domain failure. Subclasses inherit their
# parent's status via the MRO lookup in ``_status_for``.
DOMAIN_ERROR_STATUS = {
    exceptions.InvalidRequest: 400,
    exceptions.NotAuthenticated: 401,
    exceptions.PermissionDenied: 403,
    exceptions.NotFound: 404,
    exceptions.Conflict: 409,
}


def error_body(
    request: HttpRequest, *, code: str, message: str, details: list | None = None
) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": getattr(request, "request_id", None),
            "details": details,
        }
    }


def _status_for(exc: exceptions.DomainError) -> int:
    for cls in type(exc).__mro__:
        if cls in DOMAIN_ERROR_STATUS:
            return DOMAIN_ERROR_STATUS[cls]
    return 400


def error_response(
    api: NinjaAPI,
    request: HttpRequest,
    *,
    status: int,
    code: str,
    message: str,
    details: list | None = None,
) -> HttpResponse:
    body = error_body(request, code=code, message=message, details=details)
    return api.create_response(request, body, status=status)


def status_code_name(status: int) -> str:
    try:
        return HTTPStatus(status).phrase.lower().replace(" ", "_").replace("-", "_")
    except ValueError:
        return "error"


def install_exception_handlers(api: NinjaAPI) -> None:
    @api.exception_handler(ValidationError)
    def validation_error(request, exc: ValidationError):
        return error_response(
            api,
            request,
            status=422,
            code="validation_error",
            message="Request validation failed.",
            details=exc.errors,
        )

    @api.exception_handler(AuthenticationError)
    def authentication_error(request, exc: AuthenticationError):
        return error_response(
            api,
            request,
            status=401,
            code="not_authenticated",
            message="Authentication required.",
        )

    @api.exception_handler(HttpError)
    def http_error(request, exc: HttpError):
        return error_response(
            api,
            request,
            status=exc.status_code,
            code=status_code_name(exc.status_code),
            message=exc.message,
        )

    @api.exception_handler(exceptions.DomainError)
    def domain_error(request, exc: exceptions.DomainError):
        return error_response(
            api,
            request,
            status=_status_for(exc),
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    @api.exception_handler(DjangoPermissionDenied)
    def django_permission_denied(request, exc: DjangoPermissionDenied):
        return error_response(
            api, request, status=403, code="permission_denied", message="Permission denied."
        )

    @api.exception_handler(Http404)
    def not_found(request, exc: Http404):
        return error_response(api, request, status=404, code="not_found", message="Not found.")

    @api.exception_handler(Exception)
    def unexpected_error(request, exc: Exception):
        logger.exception("Unhandled API error on %s %s", request.method, request.path)
        return error_response(
            api,
            request,
            status=500,
            code="internal_error",
            message="An unexpected error occurred.",
        )
