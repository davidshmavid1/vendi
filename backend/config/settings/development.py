"""Local development. Reads backend/.env if present."""

from config.env import database_config, env_list, env_str, load_env_file

load_env_file()

from .base import *  # noqa: E402, F403

DEBUG = True
SECRET_KEY = env_str("DJANGO_SECRET_KEY", "django-insecure-development-only-key")
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

DATABASES = {"default": database_config(require_local=True)}

API_DOCS_ENABLED = True

# Next.js dev server origin, which proxies /api/v1/ to Django.
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "http://localhost:3000")
FRONTEND_BASE_URL = env_str("FRONTEND_BASE_URL", "http://localhost:3000")

# Emails (including verification/reset links) print to the runserver console.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = "Vendi <no-reply@localhost>"

# Stripe test-mode keys (optional). Without them, checkout reports that
# payments are unavailable; free stalls still work.
STRIPE_SECRET_KEY = env_str("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = env_str("STRIPE_WEBHOOK_SECRET", "")
