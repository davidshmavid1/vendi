"""Cursor pagination for list endpoints (see ARCHITECTURE.md -> API conventions).

Items are ordered by primary key; ``cursor`` is the last id the client saw.
Stable under inserts, and needs no count query.
"""

from django.db.models import QuerySet

DEFAULT_LIMIT = 50
MAX_LIMIT = 100


def paginate(queryset: QuerySet, *, cursor: int | None, limit: int | None, key: str = "pk"):
    """Return (items, next_cursor). ``key`` is the unique, ordered field the
    cursor refers to (e.g. "pk" or "organization_id")."""
    limit = min(max(limit or DEFAULT_LIMIT, 1), MAX_LIMIT)
    if cursor is not None:
        queryset = queryset.filter(**{f"{key}__gt": cursor})
    items = list(queryset.order_by(key)[: limit + 1])
    if len(items) <= limit:
        return items, None
    items = items[:limit]
    last = items[-1]
    return items, getattr(last, key)
