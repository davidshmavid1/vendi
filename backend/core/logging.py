import logging

from core.request_id import current_request_id


class RequestIDFilter(logging.Filter):
    """Adds ``request_id`` to every log record ("-" outside a request)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id.get() or "-"
        return True
