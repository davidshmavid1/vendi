"""Create the table behind Django's database cache (settings.CACHES).

The cache stores rate-limit counters so limits are shared by every app process
without adding Redis. Created by a migration so every database that runs
``migrate`` (development, CI, the pytest test database) has it.
"""

from django.core.management import call_command
from django.db import migrations

CACHE_TABLE = "vendi_cache"


def create_cache_table(apps, schema_editor):
    call_command("createcachetable", database=schema_editor.connection.alias, verbosity=0)


def drop_cache_table(apps, schema_editor):
    schema_editor.execute(f"DROP TABLE IF EXISTS {schema_editor.quote_name(CACHE_TABLE)}")


class Migration(migrations.Migration):
    dependencies = []

    operations = [migrations.RunPython(create_cache_table, drop_cache_table)]
