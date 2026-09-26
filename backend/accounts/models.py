from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower


def normalize_email(value: str) -> str:
    """Vendi's email policy: trim surrounding whitespace and lowercase the whole
    address. Applied everywhere an email is stored or looked up, and backed by a
    database CHECK so un-normalized values cannot be saved."""
    return (value or "").strip().lower()


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        email = normalize_email(email)
        if not email:
            raise ValueError("An email address is required.")
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if not extra_fields["is_staff"] or not extra_fields["is_superuser"]:
            raise ValueError("Superusers must have is_staff=True and is_superuser=True.")
        return self._create_user(email, password, **extra_fields)

    def get_by_natural_key(self, email):
        return self.get(email=normalize_email(email))


class User(AbstractUser):
    """A person's Vendi account, identified by email.

    Deliberately independent of organizations, markets and roles: one account
    can later own organizations, staff markets and run vendor businesses via
    separate membership/profile models. ``is_staff``/``is_superuser`` only
    grant Django admin access; they are not organizer permissions.
    """

    username = None
    email = models.EmailField("email address", max_length=254, unique=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta(AbstractUser.Meta):
        constraints = [
            models.CheckConstraint(
                condition=Q(email=Lower("email")) & ~Q(email=""),
                name="accounts_user_email_normalized",
                violation_error_message="Email must be non-empty and lowercase.",
            ),
        ]

    def clean(self):
        super().clean()
        self.email = normalize_email(self.email)

    @property
    def is_email_verified(self) -> bool:
        return self.email_verified_at is not None
