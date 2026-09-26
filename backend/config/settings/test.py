"""Automated tests. Uses PostgreSQL; pytest-django creates a separate
``test_<name>`` database from BACKEND_DATABASE_URL and drops it afterwards."""

from config.env import database_config, load_env_file

load_env_file()

from .base import *  # noqa: E402, F403

DEBUG = False
SECRET_KEY = "test-only-secret-key"  # noqa: S105
ALLOWED_HOSTS = ["testserver", "localhost"]

DATABASES = {"default": database_config(require_local=True)}

API_DOCS_ENABLED = True

FRONTEND_BASE_URL = "http://frontend.test"
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
DEFAULT_FROM_EMAIL = "Vendi <no-reply@vendi.test>"

# Fast hashing keeps user-creation tests quick. Never use outside tests.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
