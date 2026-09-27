"""Races between ownership-sensitive vendor operations, run in real parallel
transactions on separate database connections."""

import threading
from urllib.parse import unquote

import pytest
from django.db import connection
from django.utils import timezone

from accounts.models import User
from core.exceptions import DomainError
from vendors import services
from vendors.models import VendorMembership, VendorRole

pytestmark = pytest.mark.django_db(transaction=True)


def _run_concurrently(*calls):
    barrier = threading.Barrier(len(calls))
    results = [None] * len(calls)

    def run(index, call):
        try:
            barrier.wait()
            results[index] = call()
        except DomainError as exc:
            results[index] = exc.code
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(i, c)) for i, c in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def _user(email):
    return User.objects.create_user(email, "a-long-password-1", email_verified_at=timezone.now())


def _shop():
    owner, a, b = _user("owner@example.com"), _user("a@example.com"), _user("b@example.com")
    business = services.create_business(
        owner, name="Sunny Acres", category="PRODUCE", contact_email="hi@example.com"
    ).business
    a_m = VendorMembership.objects.create(business=business, user=a, role=VendorRole.MEMBER)
    b_m = VendorMembership.objects.create(business=business, user=b, role=VendorRole.MEMBER)
    return business, owner, a, a_m, b_m


def _owner_count(business):
    return VendorMembership.objects.filter(business=business, role=VendorRole.OWNER).count()


@pytest.mark.parametrize("_", range(3))
def test_two_simultaneous_transfers_leave_exactly_one_owner(_):
    business, owner, _a, a_m, b_m = _shop()

    results = _run_concurrently(
        lambda: services.transfer_ownership(owner, business.pk, a_m.pk),
        lambda: services.transfer_ownership(owner, business.pk, b_m.pk),
    )

    assert sorted(map(str, results))[-1] == "permission_denied"
    assert _owner_count(business) == 1


@pytest.mark.parametrize("_", range(3))
def test_transfer_racing_the_targets_leave_never_orphans_the_business(_):
    business, owner, a, a_m, _b = _shop()

    results = _run_concurrently(
        lambda: services.transfer_ownership(owner, business.pk, a_m.pk),
        lambda: services.leave_business(a, business.pk),
    )

    assert _owner_count(business) == 1
    assert results[1] == "owner_must_transfer" or results[0] == "not_found"


def test_same_invitation_accepted_twice_at_once_creates_one_membership(mailoutbox):
    business, owner, *_ = _shop()
    newcomer = _user("new@example.com")
    services.create_invitation(owner, business.pk, email="new@example.com")
    raw = unquote(mailoutbox[0].body.split("token=")[1].split()[0])

    results = _run_concurrently(
        lambda: services.accept_invitation(newcomer, raw),
        lambda: services.accept_invitation(newcomer, raw),
    )

    assert sorted(map(str, results))[-1] == "invitation_invalid"
    assert VendorMembership.objects.filter(business=business, user=newcomer).count() == 1
