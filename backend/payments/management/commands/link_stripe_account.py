"""Link an organization to its Stripe connected account (operator step).

The account must already exist; Stripe is asked directly. Checkout is
offered only while its ``transfers`` capability is active, since destination
charges transfer each payment to it (``reconcile_payments`` re-checks it).
There is no self-serve onboarding in the Django app yet.
"""

import re

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from organizations.models import Organization
from payments.gateway import ProviderError
from payments.models import STRIPE_ACCOUNT_ID, PaymentAccount
from payments.services import gateway


class Command(BaseCommand):
    help = "Link an organization to a Stripe connected account after checking it with Stripe."

    def add_arguments(self, parser):
        parser.add_argument("organization_id", type=int)
        parser.add_argument("stripe_account_id")
        parser.add_argument(
            "--fee-bps",
            type=int,
            default=100,
            help="Platform fee in basis points (default 100 = 1%%, as in the legacy app).",
        )

    def handle(self, *args, organization_id, stripe_account_id, fee_bps, **options):
        if not re.fullmatch(STRIPE_ACCOUNT_ID, stripe_account_id):
            raise CommandError("Expected a connected account id like acct_123.")
        if not 0 <= fee_bps <= 10_000:
            raise CommandError("--fee-bps must be between 0 and 10000.")
        organization = Organization.objects.filter(pk=organization_id).first()
        if organization is None:
            raise CommandError("Organization not found.")
        try:
            account = gateway().retrieve_account(stripe_account_id)
        except ProviderError as error:
            raise CommandError(f"Stripe didn't confirm the account ({error.code}).") from error
        livemode = gateway().key_livemode()
        with transaction.atomic():
            PaymentAccount.objects.update_or_create(
                organization=organization,
                defaults={
                    "stripe_account_id": account.id,
                    "livemode": livemode,
                    "charges_enabled": account.transfers_active,
                    "application_fee_bps": fee_bps,
                    "verified_at": timezone.now(),
                },
            )
        state = (
            "can receive payments"
            if account.transfers_active
            else "can NOT receive payments yet (transfers capability isn't active)"
        )
        mode = "live" if livemode else "test"
        self.stdout.write(f"Linked {organization.name} to {account.id} ({mode}); {state}.")
