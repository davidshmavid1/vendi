import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from accounts.models import User, normalize_email


def test_custom_user_model_is_configured():
    assert get_user_model() is User
    assert User.USERNAME_FIELD == "email"


def test_user_has_no_organization_market_or_role_dependency():
    field_names = {field.name for field in User._meta.get_fields()}

    for forbidden in ("organization", "organization_id", "market", "role", "username"):
        assert forbidden not in field_names


def test_email_normalization_policy():
    assert normalize_email("  Sam.Farmer@Example.COM ") == "sam.farmer@example.com"


@pytest.mark.django_db
def test_user_created_without_organization_is_normalized_and_unprivileged():
    user = User.objects.create_user(email=" Sam@Example.com", password="a-long-test-password")

    assert user.email == "sam@example.com"
    assert user.check_password("a-long-test-password")
    assert user.password.startswith("md5$")  # hashed with the configured hasher
    assert not user.is_staff and not user.is_superuser
    assert user.email_verified_at is None


@pytest.mark.django_db
def test_database_rejects_duplicate_email_even_when_validation_is_bypassed():
    User.objects.create_user(email="sam@example.com", password="x")

    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.bulk_create([User(email="sam@example.com", password="x")])


@pytest.mark.django_db
@pytest.mark.parametrize("bad_email", ["Sam@Example.com", ""])
def test_database_rejects_unnormalized_or_empty_email(bad_email):
    user = User.objects.create_user(email="sam@example.com", password="x")

    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=user.pk).update(email=bad_email)
