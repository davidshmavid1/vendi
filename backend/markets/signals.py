"""Signals other domains use to react to market changes without markets
importing them.

``occurrence_cancelled`` is sent inside the transaction that cancels a date
(``services.cancel_occurrence``), while that date's row is locked. Receivers
record their follow-up work in the same transaction, so a cancelled date
always has its cleanup recorded; they must not call external services.
Arguments: ``occurrence``, ``actor``, ``role`` (the actor's organization role).
"""

from django.dispatch import Signal

occurrence_cancelled = Signal()
