import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection

from config.env import database_config
from core.checks import check_not_prisma_database


@pytest.mark.django_db
def test_backend_database_passes_prisma_check():
    assert check_not_prisma_database(None, databases=["default"]) == []


@pytest.mark.django_db
def test_migrate_refuses_a_prisma_managed_database():
    # DDL is transactional in PostgreSQL, so this table is rolled back after the test.
    with connection.cursor() as cursor:
        cursor.execute('CREATE TABLE "_prisma_migrations" (id varchar(36) PRIMARY KEY)')

    errors = check_not_prisma_database(None, databases=["default"])

    assert [e.id for e in errors] == ["vendi.E001"]


def test_database_checks_skipped_when_no_database_requested():
    assert check_not_prisma_database(None, databases=None) == []


def test_non_local_database_refused_in_development_and_tests(monkeypatch):
    url = "postgres://user:hunter2@ep-example.neon.tech/vendi"
    monkeypatch.setenv("BACKEND_DATABASE_URL", url)
    monkeypatch.delenv("BACKEND_ALLOW_REMOTE_DATABASE", raising=False)

    with pytest.raises(ImproperlyConfigured) as excinfo:
        database_config(require_local=True)

    assert "hunter2" not in str(excinfo.value)
    assert "neon.tech" not in str(excinfo.value)


def test_non_postgres_database_refused(monkeypatch):
    monkeypatch.setenv("BACKEND_DATABASE_URL", "sqlite:///db.sqlite3")

    with pytest.raises(ImproperlyConfigured, match="postgres"):
        database_config(require_local=True)


def test_backend_ignores_prisma_database_url(monkeypatch):
    monkeypatch.delenv("BACKEND_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgres://vendi@localhost/vendi")

    with pytest.raises(ImproperlyConfigured, match="BACKEND_DATABASE_URL"):
        database_config(require_local=True)
