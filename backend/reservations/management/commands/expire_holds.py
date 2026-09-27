"""Mark lapsed stall holds EXPIRED. Optional housekeeping: holds stop
blocking stalls as soon as they lapse whether or not this runs."""

from django.core.management.base import BaseCommand

from reservations.services import expire_holds


class Command(BaseCommand):
    help = "Mark stall holds whose time is up as EXPIRED (safe to run anytime)."

    def handle(self, *args, **options):
        count = expire_holds()
        self.stdout.write(f"Expired {count} hold(s).")
