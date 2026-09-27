# Vendi backend architecture

How backend code is organized, the API contract, and the boundary with the
Next.js app. Setup is in [README.md](README.md); database rules are in
[DATABASE.md](DATABASE.md).

**Implemented today:** health endpoints, accounts (`/api/v1/auth/*`) and
organizations with team memberships (`/api/v1/organizations/*`,
`/api/v1/invitations/*`) and vendor businesses (`/api/v1/vendors/*`,
`/api/v1/vendor-invitations/*`), and organization restrictions
(`/api/v1/organizations/{id}/restrictions`). Markets and later domains are the
**intended** structure.

## Layers

| Layer | Lives in | Responsible for | Must not |
| --- | --- | --- | --- |
| API | `<app>/api.py` (a Ninja `Router`), `<app>/schemas.py` | Parse and validate input (schemas), resolve the caller (`auth=`), enforce CSRF, call **one** named operation, turn the result into a response schema, manage the session | Contain business rules or queries beyond fetching `request.auth` |
| Operations | `<app>/services.py` (plain functions) | Business rules, authorization policies, transactions, raising `core.exceptions` errors | Touch `HttpRequest`/`HttpResponse`, cookies or status codes |
| Models | `<app>/models.py` | Persisted state, relationships, database constraints, small invariant-keeping methods | Hide workflows in `save()` overrides or signals |
| Integrations | `<app>/<provider>.py`, e.g. `accounts/emails.py` | Talking to external services and translating their responses/errors | Be called inside a database transaction |

Guidelines:

- Use the Django ORM directly in operations. No repository layer that
  mirrors ORM methods.
- An operation is a function unless it needs to hold state; then use a small
  class. No service base classes.
- Critical workflows are explicit calls, never Django signals.
- Don't add layers a feature doesn't need: `core/api.py` (health) queries the
  database directly because it has no business rules.

## Domains

One Django app per domain, **created when its first model or endpoint is
built**. Organizer screens and vendor screens are two interfaces over the
same domains, not two backends.

| Domain (app) | Owns | Status |
| --- | --- | --- |
| Identity (`accounts`) | Users, credentials, email verification, sessions | Implemented |
| Organizations (`organizations`) | Organizations, memberships and roles, invitations, team audit trail | Implemented |
| Moderation (`moderation`) | Organization-scoped restrictions of accounts and vendor businesses; the participation policy other domains call | Implemented (called by application submission and approval) |
| Vendors (`vendors`) | Vendor businesses/profiles and the people authorized to manage them | Implemented |
| Markets (`markets`) | Listings, locations, event occurrences, weekly recurrence, publication, public discovery (search, filters, nearby, map markers; no PostGIS) | Implemented |
| Applications (`applications`) | Per-date intake settings and versioned questions, submissions with question and vendor snapshots, withdrawal, review decisions and their history | Implemented (Phase 10) |
| Layouts (`layouts`) | Layout versions per market (canvas, rectangular stalls; immutable once used), each date's selected version, per-date stall offers (integer minor-unit prices), publication | Implemented (Phase 11) |
| Reservations (`reservations`) | Time-limited holds on a date's stall offers with price snapshots, release, expiry without a job, and the internal `confirm_hold` operation for payments; public availability | Implemented (Phase 12) |
| Bookings (`bookings`) | Confirmed stall bookings, created only in the transaction that confirms a reservation; vendor and organizer reads | Implemented (Phase 13) |
| Payments (`payments`) | Stripe Checkout attempts (Connect destination charges), signed webhooks, verified confirmation, compensating refunds, reconciliation; `payments/gateway.py` is the only Stripe integration point | Implemented (Phase 13; subscriptions and cancellation refunds still planned) |

Domains call each other through operations (e.g. bookings calls
`applications.services.approved_application_for(...)`), not by writing
each other's tables. Cross-domain reads of models are fine; cross-domain
writes go through the owning domain's operation.

## Identity vs. authority

- A session proves **who** the caller is (`request.auth` is a `User`). It
  grants no access to any organization, market or vendor business.
- What the caller may do is decided per request by the owning domain's
  policy, from **current** database state: memberships and bans are read on
  every relevant request, never trusted from cookies, tokens or client
  input.
- An `organization_id` (or any owner id) in a URL or body identifies the
  target. It is never proof of membership.
- `is_staff`/`is_superuser` only grant Django admin access. They are not
  organizer roles and are never accepted from clients.
- A person may belong to several organizations and vendor businesses at
  once. There is no global ORGANIZER/VENDOR role.

### How organization authorization is enforced (implemented)

`organizations/permissions.py` is the one place these checks live:

1. `membership_for(user, organization_id)` loads the caller's membership from
   PostgreSQL **on every request**. No membership (or no such organization)
   → 404 `not_found`, with the same message either way.
2. `require_role(...)` / `require_manages(...)` → 403 `permission_denied` for
   a member whose role doesn't allow the action.
3. Nested resources are looked up **with** the organization id
   (`filter(pk=membership_id, organization_id=organization_id)`), so an id
   from another organization is simply not found.
4. Operations that change memberships or invitations first lock the
   organization row (`lock_organization`), then re-read the caller's
   membership inside that transaction. Removals, demotions and ownership
   transfers therefore run one at a time per organization, and a check can't
   pass against state that another request is changing.

Later organization-owned domains (markets, applications, …) reuse
`membership_for` + `require_role` with their own role rules. Being logged in,
`is_staff`/`is_superuser`, or an organization id in the request never grants
anything by itself.

## API conventions

- **One composition point:** `config/api.py` creates the `NinjaAPI` at
  `/api/v1/` and adds each domain's router (`/health`, `/auth`, …). Breaking
  changes need a new version prefix.
- **Schemas both ways:** request bodies subclass `core.schemas.InputSchema`
  (unknown fields → 422, so `is_staff`, owner ids, etc. can't be submitted).
  Responses are explicit `Schema`s listing each field. Never return a model
  wholesale.
- **Identifiers:** numeric `id` today. Public-facing resources may add a slug
  or UUID (see DATABASE.md). URLs use nouns: `/markets/{market_id}/occurrences`.
- **Time:** ISO 8601 in UTC (`2026-09-26T23:54:56.694Z`). Local event times
  are returned together with the market's IANA timezone.
- **Status codes:**

| Situation | Status | `error.code` |
| --- | --- | --- |
| Body/params fail schema validation | 422 | `validation_error` (+ `details`) |
| Business rule rejects a valid request | 400 | operation-specific, e.g. `password_invalid` |
| No or expired session | 401 | `not_authenticated` |
| Logged in but not allowed; CSRF failure | 403 | e.g. `permission_denied`, `csrf_failed`, `email_not_verified` |
| Resource missing (or hidden from this caller) | 404 | `not_found` |
| Wrong method | 405 | `method_not_allowed` |
| Conflicts with current state (duplicate, already booked) | 409 | e.g. `email_taken` |
| Rate limited | 429 (+ `Retry-After`) | `too_many_requests` |
| Unexpected failure | 500 | `internal_error` (details only in server logs) |

- **Error shape** (every error, including 404/405 outside Ninja):
  `{"error": {"code", "message", "request_id", "details"}}`. `code` is stable
  for clients to branch on. `message` is safe to show. `request_id` matches
  the `X-Request-ID` header and the server logs.
- **How expected errors reach clients:** operations raise
  `core.exceptions.InvalidRequest | NotAuthenticated | PermissionDenied |
  NotFound | Conflict` (or a subclass with its own `default_code`).
  `core/errors.py` maps the class to a status. Anything else becomes a
  generic 500.
- **Lists** return `{"items": [...], "next_cursor": <id or null>}`. Pass
  `?cursor=<next_cursor>&limit=<1-100, default 50>` for the next page
  (`core/pagination.py`, ordered by id).
- **Later, when an endpoint needs it:** retried POSTs that create
  money-moving records accept an `Idempotency-Key` header backed by a unique
  constraint. Not implemented yet.

## Transactions and external calls

- Operations open `transaction.atomic()` around the database work they
  coordinate. Views don't (`ATOMIC_REQUESTS` is off).
- External calls (email, Stripe) happen **outside** the atomic block, after
  the commit. `accounts.services.register` commits the user, then sends the
  email, so a failed send leaves a recoverable account.
- Locking and conditional-update rules for inventory and payments are in
  DATABASE.md → Transactions and concurrency.

## Illustrative example (not implemented)

```python
# markets/services.py
def publish_market(actor: User, market_id: int) -> Market:
    with transaction.atomic():
        market = Market.objects.select_for_update().filter(pk=market_id).first()
        if market is None or not organizations.policies.can_manage(actor, market.organization_id):
            raise NotFound("Market not found.")  # don't reveal others' markets
        if market.published_at is not None:
            raise Conflict("Market is already published.", code="already_published")
        market.published_at = timezone.now()
        market.save(update_fields=["published_at"])
    return market


# markets/api.py
@router.post(
    "/{market_id}/publish",
    response={200: MarketOut, 404: ErrorOut, 409: ErrorOut},
    auth=session_auth,
)
def publish(request, market_id: int):
    return services.publish_market(request.auth, market_id)
```

Authorization is checked inside the operation, against current membership,
so every caller of the operation (API, admin action, future job) gets the
same rule.

## Tests

- **Operation tests** call `services.*` directly: business rules,
  transactions, concurrency (`django_db(transaction=True)` with threads) and
  database constraints (`IntegrityError` with validation bypassed).
- **Endpoint tests** go through HTTP with `tests/conftest.py::ApiClient`,
  which **enforces CSRF** like a browser: status codes, error codes, schemas,
  cookies, what is and isn't exposed.
- All run on PostgreSQL. Contract-wide rules are in `tests/test_api_contract.py`.

## Browser integration and authentication

### Topology (planned for frontend integration)

```
Browser ──► Next.js (app origin, e.g. https://vendi.app)
              ├── pages, Server Components, legacy Server Actions (unchanged)
              └── /api/v1/*  ──forwarded──►  Django (not publicly exposed)
```

- The browser only ever talks to the Next.js origin. `/api/v1/*` is
  forwarded to Django, so Django's cookies are **same-origin**: no CORS, and
  `SameSite=Lax` cookies work.
- **Development:** Next.js on `http://localhost:3000` forwards `/api/v1/*` to
  `http://localhost:8000`. Django trusts that origin for CSRF via
  `DJANGO_CSRF_TRUSTED_ORIGINS` (default `http://localhost:3000`).
- **Production:** the same forwarding (or an equivalent reverse-proxy rule).
  Set `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS=https://<app origin>`,
  `FRONTEND_BASE_URL`, and `TRUSTED_PROXY_COUNT`.
- The forwarding rule itself (e.g. a `rewrites()` entry in `next.config.ts`)
  is added in the frontend-integration phase, after checking it against the
  bundled Next.js 16 docs (`node_modules/next/dist/docs/`), since this Next.js
  version differs from older ones. Nothing in the Next.js app changes before
  then.

### Session and CSRF behavior (implemented)

| Cookie | Purpose | Flags |
| --- | --- | --- |
| `vendi_sessionid` | Django session id (data stored in PostgreSQL) | HttpOnly, SameSite=Lax, Secure in production, 14 days |
| `vendi_csrftoken` | CSRF secret | HttpOnly, SameSite=Lax, Secure in production |

1. On app load (and after login/logout, which rotate it), the frontend
   calls `GET /api/v1/auth/csrf` → `{"csrf_token": "..."}`. Keep it in
   memory, never in `localStorage`.
2. Every POST sends it as the `X-CSRFToken` header (with
   `credentials: "same-origin"`).
3. Django Ninja exempts its routes from Django's CSRF middleware, so the
   backend checks explicitly: `core.auth.session_auth` checks CSRF on unsafe
   methods of authenticated endpoints, and every other state-changing
   endpoint calls `core.auth.enforce_csrf` first. A new POST endpoint must do
   one of the two; the endpoint tests' CSRF cases show how to verify it.
   Request-body validation (422) runs before that check, and neither changes
   state.

Login rotates the session id (`django.contrib.auth.login`). Logout deletes
the session. Deactivating a user, or changing the password, makes their other
sessions stop authenticating on the next request.

### Legacy Auth.js stays separate

Auth.js (`authjs.*` cookies, JWT) keeps running the current app. The two
systems share no secrets, cookies or sessions. Django never reads an Auth.js
token, and no accounts are merged or copied. Switching screens to the Django
API happens per workflow in a later phase. Linking legacy users to new
accounts is part of the Existing Data Migration phase.

## Rate limits (implemented)

Django Ninja's `SimpleRateThrottle` (sliding window), configured in
`settings.AUTH_RATE_LIMITS`:

| Endpoint | Per client IP | Per normalized email |
| --- | --- | --- |
| `login` | 10 / minute | 10 / 15 minutes |
| `register` | 5 / hour | — |
| `resend-verification` | 10 / hour | 3 / hour |
| `password-reset/request` | 10 / hour | 3 / hour |

- Exceeding a limit returns 429 with `Retry-After`. Windows slide, so an
  attacker can delay a victim's logins by at most 15 minutes at a time.
  There is no permanent lockout.
- **Storage:** Django's database cache (`vendi_cache` table, created by
  migration `core.0001`), so all processes and servers share counters
  without Redis. Updates are read-then-write, so under heavy concurrency a
  few extra requests can slip through. That's acceptable for abuse
  throttling; don't use it for anything that must be exact.
- **Client IP:** `REMOTE_ADDR`, unless `TRUSTED_PROXY_COUNT` (Ninja's
  `NUM_PROXIES`) says how many proxies append to `X-Forwarded-For`. Set it to
  exactly the number of proxies you control. Too high lets clients spoof
  their IP. `0` behind a proxy makes all clients share the proxy's IP.
- Email-keyed limits store a SHA-256 of the email, not the address.
