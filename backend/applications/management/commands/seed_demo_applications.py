"""Open applications on the demo markets and create a demo vendor, for local
testing of the application flow. Run ``seed_demo_markets`` first. Refuses to
run unless DEBUG is on (development settings), and never touches the
Next.js/Prisma database."""

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import User
from applications import services
from markets.management.commands.seed_demo_markets import DEMO_PASSWORD
from markets.models import EventOccurrence, MarketStatus, OccurrenceStatus
from vendors import services as vendor_services

QUESTIONS = [
    {"id": "products", "type": "short_text", "label": "What will you sell?", "required": True},
    {"id": "about", "type": "long_text", "label": "Tell us about your business", "required": False},
    {
        "id": "power",
        "type": "single_choice",
        "label": "Do you need electricity?",
        "required": True,
        "choices": ["Yes", "No"],
    },
    {
        "id": "rules",
        "type": "acknowledgement",
        "label": "I have read and accept the market rules",
        "required": True,
    },
]
DATES_PER_MARKET = 4


def _account(email: str) -> User:
    user, created = User.objects.get_or_create(
        email=email, defaults={"email_verified_at": timezone.now()}
    )
    if created:
        user.set_password(DEMO_PASSWORD)
        user.save()
    return user


class Command(BaseCommand):
    help = "Open applications on demo markets and create a demo vendor (development only)."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo_applications only runs with DEBUG on (development).")
        call_command("seed_demo_markets", stdout=self.stdout)
        organizer = User.objects.get(email="demo-organizer@example.com")
        organization = organizer.organization_memberships.get().organization
        occurrences = EventOccurrence.objects.filter(
            market__organization=organization,
            market__status=MarketStatus.PUBLISHED,
            status=OccurrenceStatus.SCHEDULED,
            starts_at__gt=timezone.now(),
        ).order_by("market_id", "starts_at")
        opened: dict[int, int] = {}
        for occurrence in occurrences:
            if opened.get(occurrence.market_id, 0) >= DATES_PER_MARKET:
                continue
            opened[occurrence.market_id] = opened.get(occurrence.market_id, 0) + 1
            services.configure_intake(
                organizer,
                organization.pk,
                occurrence.market_id,
                occurrence.pk,
                enabled=True,
                opens_at=None,
                closes_at=None,
                instructions="Tents must be 10x10 ft. Setup starts one hour before opening.",
                questions=QUESTIONS,
            )
        vendor = _account("demo-vendor@example.com")
        if not vendor.vendor_memberships.exists():
            vendor_services.create_business(
                vendor,
                name="Demo Honey Co",
                category="PRODUCE",
                contact_email="hello@demo-honey.example",
                city="Springfield",
                region="IL",
            )
        self.stdout.write(
            self.style.SUCCESS(
                "Applications open on up to 4 dates per demo market. Demo vendor: "
                "demo-vendor@example.com (organizer: demo-organizer@example.com), "
                "password as in seed_demo_markets."
            )
        )
