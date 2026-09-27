"""Production. Every deployment-specific value comes from the environment and
missing required values stop startup.

Deployment assumption: the app runs behind a TLS-terminating reverse proxy or
platform load balancer. Set TRUST_PROXY_SSL_HEADER=true only if that proxy
always sets X-Forwarded-Proto and strips any client-supplied value.
"""

from django.core.exceptions import ImproperlyConfigured

from config.env import database_config, env_bool, env_int, env_list, env_str

from .base import *  # noqa: F403

DEBUG = False
SECRET_KEY = env_str("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
# Only origins listed here may submit CSRF-protected forms (e.g. the admin).
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")

DATABASES = {"default": database_config(require_local=False)}

API_DOCS_ENABLED = env_bool("API_DOCS_ENABLED", False)

# Origin of the Next.js app; account emails link here. Never derived from
# request headers.
FRONTEND_BASE_URL = env_str("FRONTEND_BASE_URL")
if not FRONTEND_BASE_URL.startswith("https://"):
    raise ImproperlyConfigured("FRONTEND_BASE_URL must be an https:// origin in production.")

# Email delivery must be configured explicitly (e.g. SMTP credentials of a
# transactional email provider).
EMAIL_BACKEND = env_str("DJANGO_EMAIL_BACKEND")
DEFAULT_FROM_EMAIL = env_str("DEFAULT_FROM_EMAIL")
EMAIL_HOST = env_str("EMAIL_HOST", "localhost")
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = env_str("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env_str("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_TIMEOUT = 10

# Reverse proxies that append to X-Forwarded-For (for per-IP rate limits).
NINJA_NUM_PROXIES = env_int("TRUSTED_PROXY_COUNT", 0)

if env_bool("TRUST_PROXY_SSL_HEADER", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
# Start short; raise (and consider subdomains/preload) once HTTPS is confirmed
# stable for the production domain.
SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 3600)

# Stripe. STRIPE_ALLOW_LIVE must be set explicitly before live keys are used.
STRIPE_SECRET_KEY = env_str("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = env_str("STRIPE_WEBHOOK_SECRET", "")
STRIPE_ALLOW_LIVE = env_bool("STRIPE_ALLOW_LIVE", False)
