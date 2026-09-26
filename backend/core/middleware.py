from django.http import JsonResponse

from core import request_id
from core.errors import error_body

API_PREFIX = "/api/"


class RequestIDMiddleware:
    """Give every request a correlation ID.

    Reuses a valid incoming X-Request-ID header or generates one, exposes it as
    ``request.request_id`` and to log records, and returns it in the response
    X-Request-ID header. The ID is cleared on ``request_finished`` (see
    CoreConfig.ready) rather than here, so Django's own request logging, which
    runs after the middleware chain, still carries it.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        rid = request_id.resolve(request.headers.get(request_id.HEADER))
        request.request_id = rid
        request_id.current_request_id.set(rid)
        response = self.get_response(request)
        response[request_id.HEADER] = rid
        return response


class ApiJsonErrorFallbackMiddleware:
    """Give /api/ 404s and 405s produced outside Django Ninja the JSON error shape.

    Unknown API paths are rejected by Django's URL resolver and wrong methods
    by Ninja's path view, both with HTML/plain-text bodies that bypass the
    API's exception handlers.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (
            request.path.startswith(API_PREFIX)
            and response.status_code in (404, 405)
            and not response.get("Content-Type", "").startswith("application/json")
        ):
            return self._json_error(request, response)
        return response

    @staticmethod
    def _json_error(request, original):
        if original.status_code == 404:
            code, message = "not_found", "Not found."
        else:
            code, message = "method_not_allowed", "Method not allowed."
        response = JsonResponse(
            error_body(request, code=code, message=message), status=original.status_code
        )
        if original.has_header("Allow"):
            response["Allow"] = original["Allow"]
        return response
