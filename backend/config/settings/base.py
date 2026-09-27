"""Settings shared by every environment.

Environment-specific modules (development, test, production) import this and
must define SECRET_KEY, DEBUG, ALLOWED_HOSTS and DATABASES.
"""

from config.env import BACKEND_DIR

BASE_DIR = BACKEND_DIR

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "ninja",
    "core",
    "accounts",
    "organizations",
    "vendors",
    "moderation",
    "markets",
]

MIDDLEWARE = [
    "core.middleware.RequestIDMiddleware",
    "core.middleware.ApiJsonErrorFallbackMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

AUTH_USER_MODEL = "accounts.User"

# --- Sessions and CSRF (browser API) -------------------------------------
# Same-origin topology: the browser talks to Next.js, which forwards /api/v1/
# to Django (see ARCHITECTURE.md). Names are distinct from Auth.js's
# "authjs.*" cookies. Sessions live in the database (Django's default backend).
SESSION_COOKIE_NAME = "vendi_sessionid"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14  # 14 days
CSRF_COOKIE_NAME = "vendi_csrftoken"
# The frontend reads the token from GET /api/v1/auth/csrf, never from the cookie.
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"

# --- Account emails and tokens ---------------------------------------------
PASSWORD_RESET_TIMEOUT = 60 * 60  # 1 hour
EMAIL_VERIFICATION_MAX_AGE = 60 * 60 * 24 * 3  # 3 days

# --- Cache and rate limits ------------------------------------------------
# A PostgreSQL table (created by core migration 0001) so counters are shared
# across processes. See ARCHITECTURE.md -> Rate limits.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "vendi_cache",
    }
}
AUTH_RATE_LIMITS = {
    "login_ip": "10/m",
    "login_email": "10/15min",
    "register_ip": "5/h",
    "resend_verification_ip": "10/h",
    "resend_verification_email": "3/h",
    "password_reset_ip": "10/h",
    "password_reset_email": "3/h",
}
ORGANIZATION_INVITATION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days
ORGANIZATION_RATE_LIMITS = {
    "invitation_create_user": "30/h",
    "invitation_resend_user": "10/h",
}

VENDOR_INVITATION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days
VENDOR_RATE_LIMITS = {
    "invitation_create_user": "30/h",
    "invitation_resend_user": "10/h",
}

# Recurrence generation limits (markets.recurrence).
MARKET_SERIES_MAX_DAYS = 366
MARKET_SERIES_MAX_OCCURRENCES = 200

# How many reverse proxies in front of Django append to X-Forwarded-For.
# 0 = ignore the header and use REMOTE_ADDR (the header is client-controlled).
NINJA_NUM_PROXIES = 0

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Interactive API docs (/api/v1/docs) and the OpenAPI schema. Environment
# modules decide; production turns them off.
API_DOCS_ENABLED = False

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {"()": "core.logging.RequestIDFilter"},
    },
    "formatters": {
        "standard": {
            "format": "%(asctime)s %(levelname)s %(name)s [request_id=%(request_id)s] %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["request_id"],
            "formatter": "standard",
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        # Keep SQL (and its parameters) out of logs.
        "django.db.backends": {"level": "WARNING"},
    },
}
