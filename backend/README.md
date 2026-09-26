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

Optional, for the Django admin at `/admin/`: `uv run python manage.py createsuperuser`.

## Endpoints

| URL | Purpose |
| --- | --- |
| `GET /api/v1/health/live` | Process is up. Never touches the database. Always 200 if the server responds. |
| `GET /api/v1/health/ready` | Database accepts a `SELECT 1`. 200 when ready, 503 when not. No DB details in the response. |
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
return a generic 500 message; the traceback is logged server-side only.

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
    schemas.py             response schemas
    checks.py              refuses to migrate a Prisma-managed database
  accounts/                custom User model (see below)
  DATABASE.md              database ownership, conventions, proposed data model
  tests/
```

New domains (organizations, markets, …) will each become a Django app with its
own models and a Ninja `Router` added in `config/api.py`, when that phase
starts. Empty placeholder apps are intentionally not created ahead of time.

## Important decisions

**Separate databases.** The backend has its own PostgreSQL database, owned
by Django migrations. Prisma keeps owning the existing app's database. Guards
stop the backend from ever using the Prisma database. Ownership, schema
conventions, migrations, psql inspection, transactions and the proposed data
model are in [DATABASE.md](DATABASE.md).

**Custom user model now, rules later.** `accounts.User` extends Django's
`AbstractUser` with no changes. Django requires the user model to be chosen
before the first migration, and switching later is painful. It has no
organization, market or role field: in Vendi one person can own
organizations, staff markets and run vendor businesses, which will be modelled
as memberships and profiles. **Still to be decided in the independent-accounts
phase:** whether people log in with email or username, email uniqueness and
normalization, and how existing Next.js accounts get linked. No
signup/login API exists yet.

**Settings per environment.** `manage.py` defaults to development settings.
`wsgi.py`/`asgi.py` default to production, and deployments should still set
`DJANGO_SETTINGS_MODULE` explicitly.

## Production settings

`config.settings.production` refuses to start without `DJANGO_SECRET_KEY`,
`DJANGO_ALLOWED_HOSTS` and `BACKEND_DATABASE_URL`. It sets `DEBUG=False`,
secure cookies, HTTPS redirect and HSTS (1 hour by default,
`SECURE_HSTS_SECONDS`), and turns API docs off (`API_DOCS_ENABLED`).

Assumptions to revisit in the deployment phase:

- TLS is terminated by a proxy/load balancer. Set `TRUST_PROXY_SSL_HEADER=true`
  only if that proxy always sets `X-Forwarded-Proto` and strips client-sent
  values; otherwise leave it off.
- `manage.py check --deploy` warns about HSTS subdomains/preload on purpose:
  those depend on the final domain setup.
- No CORS is configured (no cross-origin access at all). `DJANGO_CSRF_TRUSTED_ORIGINS`
  is empty by default.
- Serving admin static files and choosing an app server (e.g. gunicorn) are
  deployment-phase work.

## Future Next.js ↔ Django boundary

Not implemented yet; recorded so later phases stay consistent:

- Next.js owns presentation. Django owns business operations and authorization.
- Each domain moves over whole, with exactly one authoritative writer at a
  time. There's no period where Prisma and Django both write the same data.
- Browser authentication, cookies, CSRF and how requests are routed to
  `/api/v1/` will be designed explicitly before any frontend integration.
  Until then there is no second login flow and no cross-origin access.
