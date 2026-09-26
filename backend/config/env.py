"""Small helpers for reading configuration from environment variables.

Settings modules call these instead of touching ``os.environ`` directly so that
a missing or malformed value fails at startup with a clear message.
"""

import os
from pathlib import Path
from urllib.parse import urlsplit

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BACKEND_DIR = Path(__file__).resolve().parent.parent

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}

# Hosts a development or test database may live on. Anything else (e.g. a Neon
# URL copied from the Next.js app's .env) is refused unless explicitly allowed.
LOCAL_DATABASE_HOSTS = {"", "localhost", "127.0.0.1", "::1", "postgres"}


def load_env_file() -> None:
    """Load backend/.env for local development and tests.

    Existing environment variables win, so CI and shell exports are never
    overridden. Production settings do not call this.
    """
    from dotenv import load_dotenv

    load_dotenv(BACKEND_DIR / ".env", override=False)


def env_str(name: str, default: str | None = None) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        if default is None:
            raise ImproperlyConfigured(
                f"Missing required environment variable {name}. See backend/.env.example."
            )
        return default
    return value


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    raise ImproperlyConfigured(f"Environment variable {name} must be true or false.")


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        raise ImproperlyConfigured(f"Environment variable {name} must be an integer.") from None


def env_list(name: str, default: str | None = None) -> list[str]:
    """Comma-separated list, e.g. ``ALLOWED_HOSTS=api.example.com,example.com``."""
    return [item.strip() for item in env_str(name, default).split(",") if item.strip()]


def database_config(name: str = "BACKEND_DATABASE_URL", *, require_local: bool) -> dict:
    """Build Django's DATABASES['default'] from a PostgreSQL URL.

    The backend deliberately reads BACKEND_DATABASE_URL, never DATABASE_URL,
    so it cannot pick up the Next.js/Prisma database by accident. Error
    messages never include the URL itself because it may contain a password.
    """
    url = env_str(name)
    try:
        config = dj_database_url.parse(url, conn_max_age=60, conn_health_checks=True)
    except ValueError:
        raise ImproperlyConfigured(f"{name} is not a valid database URL.") from None

    if config["ENGINE"] != "django.db.backends.postgresql":
        raise ImproperlyConfigured(f"{name} must be a postgres:// or postgresql:// URL.")

    host = urlsplit(url).hostname or ""
    if (
        require_local
        and host not in LOCAL_DATABASE_HOSTS
        and not env_bool("BACKEND_ALLOW_REMOTE_DATABASE", False)
    ):
        raise ImproperlyConfigured(
            f"{name} points at a non-local host. Development and tests must use a "
            "dedicated local database. Set BACKEND_ALLOW_REMOTE_DATABASE=true only "
            "if this is intentional and the database is disposable."
        )

    config.setdefault("OPTIONS", {})
    config["OPTIONS"].setdefault("connect_timeout", 5)
    return config
