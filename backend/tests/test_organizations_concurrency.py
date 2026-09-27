"""Races between ownership-sensitive operations, run in real parallel
transactions on separate database connections."""

import threading
from urllib.parse import unquote

import pytest
from django.db import connection
from django.utils import timezone

from accounts.models import User
from core.exceptions import DomainError
from organizations import services
from organizations.models import OrganizationMembership, Role

pytestmark = pytest.mark.django_db(transaction=True)


def _run_concurrently(*calls):
    """Start all calls at the same moment; return each result or DomainError code."""
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


def _team():
    owner, a, b = _user("owner@example.com"), _user("a@example.com"), _user("b@example.com")
    org = services.create_organization(owner, name="Riverside").organization
    a_m = OrganizationMembership.objects.create(organization=org, user=a, role=Role.ADMIN)
    b_m = OrganizationMembership.objects.create(organization=org, user=b, role=Role.STAFF)
    return org, owner, a, a_m, b_m


def _owner_count(org):
    return OrganizationMembership.objects.filter(organization=org, role=Role.OWNER).count()


@pytest.mark.parametrize("_", range(3))
def test_two_simultaneous_transfers_leave_exactly_one_owner(_):
    org, owner, _a, a_m, b_m = _team()

    results = _run_concurrently(
        lambda: services.transfer_ownership(owner, org.pk, a_m.pk),
        lambda: services.transfer_ownership(owner, org.pk, b_m.pk),
    )

    assert sorted(map(str, results))[-1] == "permission_denied"  # the loser is no longer owner
    assert _owner_count(org) == 1


@pytest.mark.parametrize("_", range(3))
def test_transfer_racing_the_targets_leave_never_orphans_the_organization(_):
    org, owner, a, a_m, _b = _team()

    results = _run_concurrently(
        lambda: services.transfer_ownership(owner, org.pk, a_m.pk),
        lambda: services.leave_organization(a, org.pk),
    )

    assert _owner_count(org) == 1
    # Either the transfer won (then a, now owner, may not leave) or the leave
    # won (then the transfer target is gone).
    assert results[1] == "owner_must_transfer" or results[0] == "not_found"


@pytest.mark.parametrize("_", range(3))
def test_removal_racing_an_invitation_by_the_removed_admin(_):
    org, owner, a, a_m, _b = _team()

    results = _run_concurrently(
        lambda: services.remove_member(owner, org.pk, a_m.pk),
        lambda: services.create_invitation(a, org.pk, email="new@example.com", role=Role.STAFF),
    )

    assert results[0] is None  # removal always succeeds
    # The invite either happened before removal (and is then unusable, since
    # acceptance rechecks the inviter) or was refused after it.
    assert results[1] == "not_found" or hasattr(results[1], "invitation")
    assert not OrganizationMembership.objects.filter(organization=org, user=a).exists()


def test_same_invitation_accepted_twice_at_once_creates_one_membership(mailoutbox):
    org, owner, *_ = _team()
    newcomer = _user("new@example.com")
    services.create_invitation(owner, org.pk, email="new@example.com", role=Role.STAFF)
    raw = unquote(mailoutbox[0].body.split("token=")[1].split()[0])

    results = _run_concurrently(
        lambda: services.accept_invitation(newcomer, raw),
        lambda: services.accept_invitation(newcomer, raw),
    )

    assert sorted(map(str, results))[-1] == "invitation_invalid"
    assert OrganizationMembership.objects.filter(organization=org, user=newcomer).count() == 1
