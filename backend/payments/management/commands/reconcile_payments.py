"""Recover payments a lost webhook or an interrupted request left behind.

Safe to run anytime and repeatedly (every step is idempotent). Until a
scheduler exists, an operator runs it; see README -> Payments -> Recovery.
"""

from django.core.management.base import BaseCommand

from bookings.cancellations import process_occurrence_cancellations
from payments.services import reconcile


class Command(BaseCommand):
    help = (
        "Process cancelled dates' pending work, then reconcile open payment attempts, "
        "refunds and webhook events with Stripe."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit", type=int, default=100, help="Most items per category per run."
        )

    def handle(self, *args, **options):
        limit = max(1, options["limit"])
        # Date cancellations first: they may create refunds that reconcile sends.
        cancellations = process_occurrence_cancellations(limit=limit)
        counts = {f"cancellation_{key}": value for key, value in cancellations.items()}
        counts.update(reconcile(limit=limit))
        self.stdout.write(" ".join(f"{key}={value}" for key, value in counts.items()))
        if counts["needs_operator"]:
            self.stderr.write(
                f"{counts['needs_operator']} refund(s) need an operator (failed at Stripe, or "
                "flagged for review: disputed, refunded outside Vendi, or over the refundable "
                "amount)."
            )
