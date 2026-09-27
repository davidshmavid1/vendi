"""Create a few demo organizations, markets and dates for local discovery
testing. Refuses to run unless DEBUG is on (development settings), and never
touches the Next.js/Prisma database."""

from datetime import time, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import User
from markets import recurrence, services
from organizations import services as org_services

DEMO_PASSWORD = "demo-password-123"  # noqa: S105 - local demo account, DEBUG only
MARKETS = [
    (
        "Riverside Farmers Market",
        "FARMERS_MARKET",
        "Riverside Park",
        "Springfield",
        "IL",
        "39.7817",
        "-89.6501",
        "America/Chicago",
        (6,),
    ),
    (
        "Downtown Night Popup",
        "POPUP",
        "Old Capitol Plaza",
        "Springfield",
        "IL",
        "39.8003",
        "-89.6437",
        "America/Chicago",
        (5,),
    ),
    (
        "Lakeside Growers",
        "FARMERS_MARKET",
        "Lakeshore Pavilion",
        "Chicago",
        "IL",
        "41.8826",
        "-87.6233",
        "America/Chicago",
        (3, 6),
    ),
    (
        "Harbor Makers Market",
        "POPUP",
        "Pier 7",
        "Seattle",
        "WA",
        "47.6062",
        "-122.3321",
        "America/Los_Angeles",
        (7,),
    ),
    (
        "Mesa Community Market",
        "FARMERS_MARKET",
        "Civic Center Lawn",
        "Mesa",
        "AZ",
        None,
        None,
        "America/Phoenix",
        (6,),
    ),
]


class Command(BaseCommand):
    help = "Seed demo markets for local discovery testing (development only)."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo_markets only runs with DEBUG on (development).")
        owner, created = User.objects.get_or_create(
            email="demo-organizer@example.com",
            defaults={"email_verified_at": timezone.now()},
        )
        if created:
            owner.set_password(DEMO_PASSWORD)
            owner.save()
        membership = owner.organization_memberships.select_related("organization").first()
        org = (
            membership.organization
            if membership
            else org_services.create_organization(owner, name="Demo Markets Co").organization
        )
        today = timezone.now().date()
        for name, kind, venue, city, region, lat, lng, tz, weekdays in MARKETS:
            if org.markets.filter(name=name).exists():
                continue
            market = services.create_market(
                owner,
                org.pk,
                name=name,
                market_type=kind,
                venue_name=venue,
                address_line1="1 Main St",
                city=city,
                region=region,
                country="US",
                latitude=Decimal(lat) if lat else None,
                longitude=Decimal(lng) if lng else None,
                timezone=tz,
            )
            services.create_series(
                owner,
                org.pk,
                market.pk,
                rule=recurrence.WeeklyRule(
                    1,
                    weekdays,
                    today + timedelta(days=1),
                    today + timedelta(days=90),
                    time(8),
                    time(13),
                ),
            )
            services.publish_market(owner, org.pk, market.pk)
            self.stdout.write(f"Created {name}")
        self.stdout.write(self.style.SUCCESS("Demo markets ready."))
