import threading

import pytest
from django.db import connection
from django.utils import timezone

from accounts.models import User
from core.exceptions import DomainError
from moderation import services
from moderation.models import OrganizationRestriction
from organizations import services as org_services
from organizations.models import OrganizationMembership, Role

pytestmark = pytest.mark.django_db(transaction=True)


def _user(email):
    return User.objects.create_user(email, "a-long-password-1", email_verified_at=timezone.now())


@pytest.mark.parametrize("_", range(3))
def test_concurrent_creation_yields_one_effective_restriction(_):
    owner, admin, target = _user("o@example.com"), _user("a@example.com"), _user("t@example.com")
    org = org_services.create_organization(owner, name="Riverside").organization
    OrganizationMembership.objects.create(organization=org, user=admin, role=Role.ADMIN)
    barrier = threading.Barrier(2)
    results = []

    def create(actor):
        try:
            barrier.wait()
            results.append(
                services.create_restriction(actor, org.pk, account_id=target.pk, reason="r").pk
            )
        except DomainError as exc:
            results.append(exc.code)
        finally:
            connection.close()

    threads = [threading.Thread(target=create, args=(a,)) for a in (owner, admin)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert "already_restricted" in results
    assert OrganizationRestriction.objects.effective().filter(account=target).count() == 1
