"""Read-only queries for anonymous visitors.

Visible: markets with status PUBLISHED, and their occurrences. Drafts and
archived markets (and their occurrences) are not found (404). Cancelled
occurrences stay visible with their status and message so visitors learn
the date is off. Moderation restrictions don't affect browsing.
"""

from django.utils import timezone

from core.exceptions import NotFound
from markets.models import EventOccurrence, Market, MarketStatus


def published_markets():
    return Market.objects.filter(status=MarketStatus.PUBLISHED).select_related("organization")


def get_market(market_id: int) -> Market:
    market = published_markets().filter(pk=market_id).first()
    if market is None:
        raise NotFound("Market not found.")
    return market


def upcoming_occurrences(market_id: int):
    """Dates that haven't ended yet, cancelled ones included."""
    market = get_market(market_id)
    return market.occurrences.filter(ends_at__gt=timezone.now())


def get_occurrence(occurrence_id: int) -> EventOccurrence:
    occurrence = (
        EventOccurrence.objects.select_related("market__organization")
        .filter(pk=occurrence_id, market__status=MarketStatus.PUBLISHED)
        .first()
    )
    if occurrence is None:
        raise NotFound("Event date not found.")
    return occurrence
