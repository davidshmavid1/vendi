from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """A person's Vendi account.

    Deliberately independent of organizations, markets and roles: one account
    can later own organizations, staff markets and run vendor businesses via
    separate membership/profile models. Login identifier, email uniqueness and
    account-linking rules are decided in the independent-accounts phase; this
    exists now because Django's user model must be chosen before the first
    migration.
    """
