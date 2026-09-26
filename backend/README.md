# Vendi backend (Django)

The new backend for Vendi: Django 5.2 LTS + Django Ninja, backed by its own
PostgreSQL database. It runs **alongside** the existing Next.js app, which is
unchanged and still uses Prisma, Auth.js and Stripe for everything today.

This is Phase 1 (foundation) only: configuration, database, a versioned API
with health endpoints, error handling, logging and tests. No business features
yet.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — installs Python 3.13 and dependencies for you.
- **Python 3.13** (pinned in `.python-version`; uv downloads it if missing).
- PostgreSQL 16, either via **Docker** (recommended) or an existing local install.

## Setup

All commands run from `backend/`.

```bash
cd backend
cp .env.example .env          # then edit POSTGRES_PASSWORD and BACKEND_DATABASE_URL to match
uv sync                       # creates .venv and installs locked dependencies
```

`backend/.env` is git-ignored. It is separate from the Next.js app's root
`.env`; the backend never reads the root file.

### Start PostgreSQL (Docker)

```bash
docker compose up -d          # starts postgres:16 on localhost:5433
docker compose ps             # wait for "healthy"
```

Data lives in the named volume `backend-postgres-data` and survives restarts.

### Or: use an existing local PostgreSQL

```bash
createuser --createdb --pwprompt vendi   # CREATEDB lets the test suite create its test database
createdb -O vendi vendi_backend_dev
```

Then set `BACKEND_DATABASE_URL=postgres://vendi:<password>@localhost:5432/vendi_backend_dev`
in `backend/.env`.

### Apply migrations and run

```bash
uv run python manage.py migrate
uv run python manage.py runserver     # http://localhost:8000
```

Optional, for the Django admin at `/admin/`: `uv run python manage.py createsuperuser`
(asks for an email and password).

## Endpoints

| URL | Purpose |
| --- | --- |
| `GET /api/v1/health/live` | Process is up. Never touches the database. Always 200 if the server responds. |
| `GET /api/v1/health/ready` | Database accepts a `SELECT 1`. 200 when ready, 503 when not. No DB details in the response. |
| `/api/v1/auth/*` | Accounts: see [Accounts](#accounts) below. |
| `/api/v1/docs` | Interactive API docs (Swagger UI). Development only. |
| `/api/v1/openapi.json` | OpenAPI schema. Development only. |

Every response includes an `X-Request-ID` header. Send your own (letters,
digits, `.`, `_`, `-`, up to 128 chars) to trace a request across services;
otherwise one is generated. Log lines include it as `[request_id=...]`.

### Error format

All API errors share one shape:

```json
{"error": {"code": "validation_error", "message": "Request validation failed.",
           "request_id": "…", "details": [ … ]}}
```

Validation errors (422) include field-level `details`. Unexpected errors
return a generic 500 message; the traceback is logged server-side only. Status
codes and error codes are listed in [ARCHITECTURE.md](ARCHITECTURE.md#api-conventions).

## Accounts

Independent accounts that log in by email. An account belongs to no
organization, market or vendor business and has no organizer/vendor role.
Browser session and CSRF details are in
[ARCHITECTURE.md](ARCHITECTURE.md#session-and-csrf-behavior-implemented); rate
limits are in [ARCHITECTURE.md](ARCHITECTURE.md#rate-limits-implemented).

| Endpoint | Body | Success | Notable errors |
| --- | --- | --- | --- |
| `GET /auth/csrf` | — | 200 `{"csrf_token"}` | — |
| `POST /auth/register` | `{"email", "password"}` | 201 `{"account", "verification_email_sent"}` | 400 `email_invalid`/`password_invalid`, 409 `email_taken`, 429 |
| `POST /auth/verify-email` | `{"token"}` | 200 account | 400 `verification_token_expired`/`verification_token_invalid` |
| `POST /auth/resend-verification` | `{"email"}` | 202 generic message | 429 |
| `POST /auth/login` | `{"email", "password"}` | 200 account + session cookie | 400 `invalid_credentials`, 403 `email_not_verified`, 429 |
| `GET /auth/me` | — | 200 account | 401 `not_authenticated` |
| `POST /auth/logout` | — | 204 | — |
| `POST /auth/password-reset/request` | `{"email"}` | 202 generic message | 429 |
| `POST /auth/password-reset/confirm` | `{"uid", "token", "new_password"}` | 204 | 400 `reset_token_invalid`/`password_invalid` |
| `POST /auth/password/change` | `{"current_password", "new_password"}` | 204 | 400 `current_password_incorrect`/`password_invalid`, 401 |

Paths are under `/api/v1`. Every POST needs the `X-CSRFToken` header
(`403 csrf_failed` otherwise). The account object is always exactly:

```json
{"id": 1, "email": "sam@example.com", "email_verified": true, "date_joined": "2026-09-26T23:54:56.694Z"}
```

**Email policy:** addresses are trimmed and lowercased before being stored or
looked up. PostgreSQL enforces uniqueness and the normalized form. Changing an
account's email is not supported yet.

**Registration and verification:** `register` creates an unverified account
and emails a link to `FRONTEND_BASE_URL/verify-email?token=…` (valid 3 days).
It does not log in, and login returns `403 email_not_verified` until the link
is used. The token is signed with `django.core.signing` and works once. If
the email couldn't be sent, the account still exists,
`verification_email_sent` is `false`, and `resend-verification` sends a new
link. `true` means the email backend accepted the message, not that it was
delivered.

**Password recovery:** `password-reset/request` always answers with the same
202, whether or not the email has an account. The link
(`FRONTEND_BASE_URL/reset-password?uid=…&token=…`, Django's built-in reset
token) expires after 1 hour and stops working once used. Confirming sets the
password, logs out every existing session and does **not** log in.
`password/change` keeps the current session and logs out all others.

**Deactivation:** set `is_active` to false in the admin. The account can't log
in, and its existing sessions stop working.

### Local email

In development, emails (with their links) are printed to the `runserver`
console. Nothing is sent. Tests use Django's in-memory outbox.

### Try it with curl

```bash
B=http://localhost:8000/api/v1/auth; J=/tmp/vendi-cookies
csrf() { curl -s -c $J -b $J $B/csrf | python3 -c "import sys,json;print(json.load(sys.stdin)['csrf_token'])"; }
post() { curl -s -c $J -b $J -H "Content-Type: application/json" -H "X-CSRFToken: $(csrf)" \
              -H "Origin: http://localhost:8000" -X POST "$B/$1" -d "$2"; echo; }

post register '{"email":"me@example.com","password":"correct-horse-battery-staple"}'
# copy the token from the verify-email link printed in the runserver console (URL-decode %3A to :)
post verify-email '{"token":"<token>"}'
post login '{"email":"me@example.com","password":"correct-horse-battery-staple"}'
curl -s -b $J $B/me; echo
post logout ''
```

The `Origin` header stands in for the browser. Django's CSRF check compares
it with the host or `DJANGO_CSRF_TRUSTED_ORIGINS`.

## Checks and tests

```bash
uv run ruff check .                                   # lint
uv run ruff format --check .                          # formatting (drop --check to fix)
uv run python manage.py check                         # Django system checks
uv run python manage.py makemigrations --check --dry-run   # fails if models changed without a migration
uv run pytest                                         # tests (needs PostgreSQL running)
```

Tests run against PostgreSQL, not SQLite. pytest-django creates a temporary
`test_<your db name>` database and drops it afterwards; your development data
is not touched. CI runs the same steps (`.github/workflows/backend.yml`) on
changes under `backend/`.

## Stopping

```bash
docker compose stop           # stops PostgreSQL, keeps data
docker compose down           # removes the container, still keeps the data volume
```

Only `docker compose down -v` deletes the data volume. Don't use it unless you
mean to wipe the backend development database.

## Structure

```
backend/
  manage.py
  config/                  project configuration
    env.py                 reads environment variables; clear errors when missing
    settings/
      base.py              shared by all environments
      development.py       DEBUG on, API docs on, reads backend/.env (default for manage.py)
      test.py              used by pytest
      production.py        everything from env vars, secure defaults
    api.py                 the NinjaAPI at /api/v1/ — add domain routers here
    urls.py
  core/                    cross-cutting pieces
    api.py                 health endpoints
    errors.py              JSON error handlers
    middleware.py, request_id.py, logging.py   request IDs in responses and logs
    schemas.py             shared schemas (errors, InputSchema)
    exceptions.py          domain errors raised by operations
    auth.py                session auth + CSRF enforcement for the API
    checks.py              refuses to migrate a Prisma-managed database
    migrations/            creates the database cache table (rate limits)
  accounts/                email-login User, account API
    models.py              User, email normalization
    services.py            register, verify, login, password reset/change
    api.py, schemas.py     /api/v1/auth endpoints
    emails.py, tokens.py   account emails and their signed tokens
    throttles.py           rate limits
  ARCHITECTURE.md          layers, domains, API conventions, auth topology
  DATABASE.md              database ownership, conventions, proposed data model
  tests/
```

Where new code goes, and how layers and domains interact, is in
[ARCHITECTURE.md](ARCHITECTURE.md). Empty placeholder apps are intentionally
not created ahead of time.

## Important decisions

**Separate databases.** The backend has its own PostgreSQL database, owned
by Django migrations. Prisma keeps owning the existing app's database. Guards
stop the backend from ever using the Prisma database. Ownership, schema
conventions, migrations, psql inspection, transactions and the proposed data
model are in [DATABASE.md](DATABASE.md).

**Independent accounts.** `accounts.User` logs in by email and has no
organization, market or role field. In Vendi one person can own
organizations, staff markets and run vendor businesses, which later phases
model as memberships and profiles. Existing Next.js accounts are **not**
migrated or linked; that is the Existing Data Migration phase.

**Settings per environment.** `manage.py` defaults to development settings.
`wsgi.py`/`asgi.py` default to production, and deployments should still set
`DJANGO_SETTINGS_MODULE` explicitly.

## Production settings

`config.settings.production` refuses to start without `DJANGO_SECRET_KEY`,
`DJANGO_ALLOWED_HOSTS`, `BACKEND_DATABASE_URL`, `FRONTEND_BASE_URL` (https),
`DJANGO_EMAIL_BACKEND` and `DEFAULT_FROM_EMAIL`. SMTP settings are
`EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` and
`EMAIL_USE_TLS`. It sets `DEBUG=False`,
secure cookies, HTTPS redirect and HSTS (1 hour by default,
`SECURE_HSTS_SECONDS`), and turns API docs off (`API_DOCS_ENABLED`).

Assumptions to revisit in the deployment phase:

- TLS is terminated by a proxy/load balancer. Set `TRUST_PROXY_SSL_HEADER=true`
  only if that proxy always sets `X-Forwarded-Proto` and strips client-sent
  values; otherwise leave it off.
- `manage.py check --deploy` warns about HSTS subdomains/preload on purpose:
  those depend on the final domain setup.
- No CORS is configured (no cross-origin access at all). The browser reaches
  Django through the Next.js origin. Set `DJANGO_CSRF_TRUSTED_ORIGINS` to that
  origin and `TRUSTED_PROXY_COUNT` to the number of proxies in front of Django.
- Production email needs a real provider (SMTP credentials). Sending is
  synchronous until the Background Workers phase adds durable delivery.
- Serving admin static files and choosing an app server (e.g. gunicorn) are
  deployment-phase work.

## Next.js ↔ Django boundary

Next.js owns presentation, and Django owns business operations and
authorization. Each domain moves over whole, with exactly one authoritative
writer at a time. The request routing and the browser session/CSRF flow are
in [ARCHITECTURE.md](ARCHITECTURE.md#browser-integration-and-authentication).
The existing Auth.js login is unchanged and shares nothing with the Django
accounts.

Still to do for frontend integration: the `/api/v1` forwarding rule in
Next.js, the sign-up/verify/login/reset screens that call these endpoints,
and moving each workflow off Auth.js/Prisma.
