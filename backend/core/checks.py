"""System checks that protect databases the backend must not manage."""

from django.core.checks import Error, Tags, register
from django.db import DatabaseError, connections

PRISMA_MIGRATIONS_TABLE = "_prisma_migrations"


@register(Tags.database)
def check_not_prisma_database(app_configs, databases=None, **kwargs):
    """Refuse to operate on the Next.js app's Prisma-managed database.

    Database checks run during ``migrate`` (and ``check --database``), so this
    stops Django from creating tables in a database that Prisma migrations own,
    whether local or remote.
    """
    errors = []
    for alias in databases or []:
        try:
            tables = connections[alias].introspection.table_names()
        except DatabaseError:
            continue  # Connection problems surface through migrate itself.
        if PRISMA_MIGRATIONS_TABLE in tables:
            errors.append(
                Error(
                    f"Database '{alias}' contains the {PRISMA_MIGRATIONS_TABLE} table, so it "
                    "is managed by the Next.js app's Prisma migrations.",
                    hint="Point BACKEND_DATABASE_URL at the dedicated backend database. "
                    "See backend/DATABASE.md.",
                    id="vendi.E001",
                )
            )
    return errors
