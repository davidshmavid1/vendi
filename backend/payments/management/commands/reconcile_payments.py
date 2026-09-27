"""Recover payments a lost webhook or an interrupted request left behind.

Safe to run anytime and repeatedly (every step is idempotent). Until a
scheduler exists, an operator runs it; see README -> Payments -> Recovery.
"""

from django.core.management.base import BaseCommand

from payments.services import reconcile


class Command(BaseCommand):
    help = "Reconcile open payment attempts, refunds and webhook events with Stripe."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit", type=int, default=100, help="Most items per category per run."
        )

    def handle(self, *args, **options):
        counts = reconcile(limit=max(1, options["limit"]))
        self.stdout.write(" ".join(f"{key}={value}" for key, value in counts.items()))
        if counts["needs_operator"]:
            self.stderr.write(
                f"{counts['needs_operator']} refund(s) failed at Stripe and need an operator."
            )
