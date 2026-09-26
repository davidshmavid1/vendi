from core import request_id


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
