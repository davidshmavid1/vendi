import threading
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from accounts.models import User
from core.exceptions import DomainError
from markets import recurrence, services
from markets.models import EventOccurrence, RecurrenceSeries
from organizations import services as org_services

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("_", range(3))
def test_identical_series_requests_at_once_generate_once(_):
    owner = User.objects.create_user(
        "o@example.com", "pw-long-enough-1", email_verified_at=timezone.now()
    )
    org = org_services.create_organization(owner, name="Riverside").organization
    market = services.create_market(
        owner, org.pk, name="M", market_type="POPUP", timezone="America/Chicago"
    )
    today = timezone.now().date()
    rule = recurrence.WeeklyRule(
        1,
        (3, 6),
        today + timedelta(days=7),
        today + timedelta(days=60),
        timezone.datetime.strptime("08:00", "%H:%M").time(),
        timezone.datetime.strptime("12:00", "%H:%M").time(),
    )
    barrier = threading.Barrier(2)
    results = []

    def generate():
        try:
            barrier.wait()
            results.append(services.create_series(owner, org.pk, market.pk, rule=rule))
        except DomainError as exc:  # pragma: no cover - would fail the assertions
            results.append(exc.code)
        finally:
            connection.close()

    threads = [threading.Thread(target=generate) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(r.already_existed for r in results) == [False, True]
    assert RecurrenceSeries.objects.filter(market=market).count() == 1
    expected = len(recurrence.local_dates(rule))
    assert EventOccurrence.objects.filter(market=market).count() == expected
