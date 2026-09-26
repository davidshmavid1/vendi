import pytest
from django.contrib.auth import get_user_model

from accounts.models import User


def test_custom_user_model_is_configured():
    assert get_user_model() is User


def test_user_has_no_organization_market_or_role_dependency():
    field_names = {field.name for field in User._meta.get_fields()}

    for forbidden in ("organization", "organization_id", "market", "role"):
        assert forbidden not in field_names


@pytest.mark.django_db
def test_user_can_be_created_without_organization():
    user = User.objects.create_user(username="sam", password="a-long-test-password")

    assert user.pk is not None
    assert user.check_password("a-long-test-password")
