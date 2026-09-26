"""Production. Every deployment-specific value comes from the environment and
missing required values stop startup.

Deployment assumption: the app runs behind a TLS-terminating reverse proxy or
platform load balancer. Set TRUST_PROXY_SSL_HEADER=true only if that proxy
always sets X-Forwarded-Proto and strips any client-supplied value.
"""

from config.env import database_config, env_bool, env_int, env_list, env_str

from .base import *  # noqa: F403

DEBUG = False
SECRET_KEY = env_str("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
# Only origins listed here may submit CSRF-protected forms (e.g. the admin).
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")

DATABASES = {"default": database_config(require_local=False)}

API_DOCS_ENABLED = env_bool("API_DOCS_ENABLED", False)

if env_bool("TRUST_PROXY_SSL_HEADER", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
# Start short; raise (and consider subdomains/preload) once HTTPS is confirmed
# stable for the production domain.
SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 3600)
