"""Cursor pagination for list endpoints (see ARCHITECTURE.md -> API conventions).

Items are ordered by a unique key (the primary key by default); ``cursor`` is
that key's value on the last item the client saw (an id, or e.g. a start
time for per-market event lists). Stable under inserts, no count query.
"""

from django.db.models import QuerySet

DEFAULT_LIMIT = 50
MAX_LIMIT = 100


def paginate(queryset: QuerySet, *, cursor, limit: int | None, key: str = "pk"):
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
