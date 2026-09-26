"""Consistent JSON error responses for the API.

Every error has the shape ``{"error": {"code", "message", "request_id", "details"}}``.
Expected errors keep their status code and message; unexpected ones are logged
with a traceback server-side and return a generic 500 message.
"""

import logging
from http import HTTPStatus

from django.http import Http404, HttpRequest, HttpResponse
from ninja import NinjaAPI
from ninja.errors import HttpError, ValidationError

logger = logging.getLogger(__name__)


def error_response(
    api: NinjaAPI,
    request: HttpRequest,
    *,
    status: int,
    code: str,
    message: str,
    details: list | None = None,
) -> HttpResponse:
    body = {
        "error": {
            "code": code,
            "message": message,
            "request_id": getattr(request, "request_id", None),
            "details": details,
        }
    }
    return api.create_response(request, body, status=status)


def _status_code(status: int) -> str:
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

    @api.exception_handler(HttpError)
    def http_error(request, exc: HttpError):
        return error_response(
            api,
            request,
            status=exc.status_code,
            code=_status_code(exc.status_code),
            message=exc.message,
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
