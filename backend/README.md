# Vendi backend (Django)

The new backend for Vendi: Django 5.2 LTS + Django Ninja, backed by its own
PostgreSQL database. It runs **alongside** the existing Next.js app, which is
unchanged and still uses Prisma, Auth.js and Stripe for everything today.

Implemented so far: the foundation (configuration, database, versioned API,
errors, logging, tests), independent email accounts with session login, and
organizations with team memberships and invitations, shared vendor
business profiles, organization-scoped participation restrictions, and
markets with scheduled and recurring event dates. Markets, applications, bookings and payments come in later
phases.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — installs Python 3.13 and dependencies for you.
- **Python 3.13** (pinned in `.python-version`; uv downloads it if missing).
- PostgreSQL 16 running locally (Homebrew, Postgres.app or any install). Docker
  is optional, not required.

## Setup

All commands run from `backend/`.

```bash
cd backend
cp .env.example .env          # then edit POSTGRES_PASSWORD and BACKEND_DATABASE_URL to match
uv sync                       # creates .venv and installs locked dependencies
```

`backend/.env` is git-ignored. It is separate from the Next.js app's
`frontend/.env`; the backend never reads the frontend file.

### Start PostgreSQL (local install)

With Homebrew (Postgres.app works the same way):

```bash
brew install postgresql@16
brew services start postgresql@16
pg_isready
createuser --createdb --pwprompt vendi
createdb -O vendi vendi_backend_dev
```

`CREATEDB` lets the test suite create its temporary test database. Set
`BACKEND_DATABASE_URL=postgres://vendi:<password>@localhost:5432/vendi_backend_dev`
in `backend/.env`.

If `brew services` reports a status of `other` and `pg_isready` says
`no response`, start it directly instead (and again after a reboot):
`pg_ctl -D /opt/homebrew/var/postgresql@16 -l /opt/homebrew/var/log/postgresql@16.log start`.

### Or: Docker (optional)

```bash
docker compose up -d          # postgres:16 on localhost:5433, data in a named volume
```

Set the `POSTGRES_*` values in `backend/.env` and point `BACKEND_DATABASE_URL`
at port 5433.

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
| `/api/v1/organizations/*`, `/api/v1/invitations/*` | Organizations and teams: see [below](#organizations-and-teams). |
| `/api/v1/vendors/*`, `/api/v1/vendor-invitations/*` | Vendor businesses: see [below](#vendor-businesses). |
| `/api/v1/organizations/{id}/restrictions` | Organization restrictions: see [below](#organization-restrictions-moderation). |
| `/api/v1/organizations/{id}/markets/*`, `/api/v1/public/*` | Markets and event dates: see [below](#markets-and-event-dates). |
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

## Organizations and teams

An **organization** operates markets. People get authority in an organization
only through a **membership** with one role: `OWNER`, `ADMIN` or `STAFF`.
Accounts stay independent: one person can belong to several organizations
with a different role in each, and nothing about organizations is stored on
the user. Each organization has exactly one owner, and the owner membership
*is* the ownership record. Vendor businesses and organization restrictions
have their own sections below. The existing Next.js organizer screens and legacy
organizations are **not** migrated.

| Action | OWNER | ADMIN | STAFF |
| --- | --- | --- | --- |
| View organization and member directory | ✓ | ✓ | ✓ (no emails) |
| Rename organization | ✓ | ✓ | — |
| See pending invitations | ✓ | ✓ | — |
| Invite, resend, revoke | ADMIN or STAFF | STAFF | — |
| Remove a member | ADMIN or STAFF | STAFF | — |
| Change a role (ADMIN ↔ STAFF) | ✓ | — | — |
| Transfer ownership | ✓ | — | — |
| Leave | after transferring ownership | ✓ | ✓ |

Checks read the caller's current membership on every request, so a removed
or demoted member loses access on their very next request without logging
out. Platform `is_staff`/`is_superuser` grant nothing here. Non-members get
404 for everything about an organization. Members get 403 for actions their
role doesn't allow. See
[ARCHITECTURE.md](ARCHITECTURE.md#how-organization-authorization-is-enforced-implemented).

| Endpoint (under `/api/v1`) | Body | Success |
| --- | --- | --- |
| `POST /organizations` | `{"name", "slug"?}` | 201 `{"organization", "membership_id", "role": "OWNER"}` |
| `GET /organizations` | `?cursor&limit` | 200 `{"items": [{"organization", "membership_id", "role"}], "next_cursor"}` |
| `GET /organizations/{id}` | — | 200 organization + your role |
| `PATCH /organizations/{id}` | `{"name"}` | 200 |
| `GET /organizations/{id}/members` | `?cursor&limit` | 200 `{"items": [{"membership_id", "user_id", "name", "role", "joined_at", "email"}]}` |
| `PATCH /organizations/{id}/members/{membership_id}` | `{"role": "ADMIN" \| "STAFF"}` | 200 member |
| `DELETE /organizations/{id}/members/{membership_id}` | — | 204 |
| `POST /organizations/{id}/leave` | — | 204 |
| `POST /organizations/{id}/transfer-ownership` | `{"membership_id"}` | 200 new owner |
| `POST /organizations/{id}/invitations` | `{"email", "role"}` | 201 `{"invitation", "email_sent"}` |
| `GET /organizations/{id}/invitations` | `?cursor&limit` | 200 pending invitations |
| `POST /organizations/{id}/invitations/{invitation_id}/resend` | — | 200 `{"invitation", "email_sent"}` |
| `DELETE /organizations/{id}/invitations/{invitation_id}` | — | 204 |
| `POST /invitations/accept` | `{"token"}` | 200 `{"organization", "membership_id", "role", "already_member"}` |

All need a session. Every POST, PATCH and DELETE needs `X-CSRFToken`.
Notable errors: `slug_taken`, `owner_role_locked`, `cannot_remove_self`,
`owner_must_transfer`, `transfer_target_ineligible`, `already_member`,
`invitation_pending`, `invitation_not_pending`, `invitation_invalid`,
`invitation_email_mismatch` and `email_not_verified`.

**Creating:** needs a verified account. The organization, the owner membership
and an audit record are created in one transaction. The slug comes from the
name (with a random suffix if taken) unless you pass one. A requested slug
that's taken is `409 slug_taken`. Slugs can't be changed yet.

**Invitation lifecycle:**

1. An OWNER/ADMIN invites an email with a role. The email is normalized like
   account emails. Only one pending invitation per email per organization is
   allowed (`409 invitation_pending`, so resend instead). An expired one is
   marked `EXPIRED` and replaced. Inviting an existing member is
   `409 already_member`.
2. The email links to `FRONTEND_BASE_URL/invitations/accept?token=…`. The
   token is 256 random bits. Only its SHA-256 is stored, and it never appears
   in API responses or logs. The link is valid for 7 days. No account or
   membership is created yet.
3. **Resend** rotates the token: the old link stops working and the 7 days
   restart. If sending failed (`email_sent: false`), resending is the
   recovery.
4. **Accept** needs a logged-in, verified account whose email equals the
   invited one. People without an account sign up and verify first, then
   open the link again. Acceptance rechecks that the inviter is *still* a
   member allowed to grant that role; if not, the link is invalid. An
   existing member keeps their current role (`already_member: true`). A used,
   revoked, expired or tampered link returns `400 invitation_invalid`.
5. **Revoke** marks a pending invitation `REVOKED`.

**Ownership:** only the owner can transfer, to an existing active member
with a verified email. In one transaction the old owner becomes ADMIN and the
target becomes OWNER. The owner can't leave or be removed without
transferring first. A partial unique index blocks a second owner, and
organization-row locking plus these rules keep one owner at all times. The
one-owner index can't by itself guarantee an owner *exists*.

**Audit:** creation, rename, invitation create/resend/revoke/accept, role
changes, removals, departures and ownership transfers each write an
`OrganizationAuditEvent` (actor, organization, subject, action, time, role
details) in the same transaction as the change. No tokens or email
addresses are stored in it. Viewing them is Phase 18 work; for now use the
Django admin or psql.

**Rate limits:** each user may create 30 invitations per hour and resend 10
per hour (`ORGANIZATION_RATE_LIMITS`).

**Try it locally:** with the `post` helper from [Try it with curl](#try-it-with-curl),
logged in as a verified user:

```bash
O=http://localhost:8000/api/v1/organizations
post() { curl -s -c $J -b $J -H "Content-Type: application/json" -H "X-CSRFToken: $(csrf)" \
              -H "Origin: http://localhost:8000" -X "${3:-POST}" "$1" -d "$2"; echo; }
post $O '{"name":"Riverside Market"}'                          # -> organization id, e.g. 1
post $O/1/invitations '{"email":"friend@example.com","role":"STAFF"}'
# the invitation link is printed in the runserver console; as friend@example.com
# (registered, verified, logged in with a separate cookie jar):
post http://localhost:8000/api/v1/invitations/accept '{"token":"<token>"}'
curl -s -b $J $O/1/members; echo
```

## Vendor businesses

A **vendor business** is a vendor's shared profile (name, description,
category, contact details, city/region). It belongs to no organization or
market, so one profile can later apply to many markets. People manage it
through a **vendor membership** with one role:

| Action | OWNER | MEMBER |
| --- | --- | --- |
| View the private profile and member directory | ✓ | ✓ (no member emails) |
| Update the profile | ✓ | — |
| Invite, list, resend, revoke invitations | ✓ | — |
| Remove a MEMBER | ✓ | — |
| Transfer ownership | ✓ | — |
| Leave | after transferring ownership | ✓ |

There is exactly one owner per business (a partial unique index blocks a
second one; creation, transfer and leave rules keep one in place). One
account can own or belong to several businesses and hold organization
memberships at the same time: there is no global "vendor" account type, and
organization roles grant nothing on vendor businesses (or the other way
round). Nothing creates a business automatically; a person creates one
when they want to sell. Non-members get 404, and members get 403 for
owner-only actions. The profile, including its contact email, is visible
only to the business's members. There are no public vendor pages yet.

**Category** is one of: `PRODUCE`, `MEAT_DAIRY_EGGS`, `BAKED_GOODS`,
`PREPARED_FOOD`, `BEVERAGES`, `CRAFTS`, `FLOWERS_PLANTS`, `HEALTH_BEAUTY` or
`OTHER`. `contact_email` is the business's public-facing contact address,
normalized like account emails and unrelated to anyone's login email.

| Endpoint (under `/api/v1`) | Body | Success |
| --- | --- | --- |
| `POST /vendors` | profile fields | 201 `{"business", "membership_id", "role": "OWNER"}` |
| `GET /vendors` | `?cursor&limit` | 200 `{"items": [{"business", "membership_id", "role"}], "next_cursor"}` |
| `GET /vendors/{id}` | — | 200 business + your role |
| `PATCH /vendors/{id}` | any profile fields (owner) | 200 |
| `GET /vendors/{id}/members` | `?cursor&limit` | 200 `{"items": [{"membership_id", "user_id", "name", "role", "joined_at", "email"}]}` |
| `DELETE /vendors/{id}/members/{membership_id}` | — | 204 |
| `POST /vendors/{id}/leave` | — | 204 |
| `POST /vendors/{id}/transfer-ownership` | `{"membership_id"}` | 200 new owner |
| `POST /vendors/{id}/invitations` | `{"email"}` | 201 `{"invitation", "email_sent"}` |
| `GET /vendors/{id}/invitations` | `?cursor&limit` | 200 pending invitations |
| `POST /vendors/{id}/invitations/{invitation_id}/resend` | — | 200 `{"invitation", "email_sent"}` |
| `DELETE /vendors/{id}/invitations/{invitation_id}` | — | 204 |
| `POST /vendor-invitations/accept` | `{"token"}` | 200 `{"business", "membership_id", "role", "already_member"}` |

Create:

```json
{"name": "Sunny Acres Farm", "category": "PRODUCE", "contact_email": "hello@sunnyacres.example",
 "description": "Seasonal vegetables", "phone": "+1 555 010 2000",
 "website": "https://sunnyacres.example", "city": "Springfield", "region": "IL"}
```

Update (send only the fields to change; `""` clears an optional field):

```json
{"description": "Now with eggs", "city": ""}
```

Only these profile fields are accepted. Anything else (`id`, `role`,
`owner`, timestamps, account ids) is rejected with 422. Validation errors:
`name_invalid`, `contact_email_invalid`, `phone_invalid`, `website_invalid`
(must be `http(s)://`). Other codes: `owner_must_transfer`, `already_owner`,
`transfer_target_ineligible`, `already_member`, `invitation_pending`,
`invitation_not_pending`, `invitation_invalid`, `invitation_email_mismatch`,
`email_not_verified`.

**Invitations** work like organization invitations. The role is always
MEMBER. The link is `FRONTEND_BASE_URL/vendor-invitations/accept?token=…`
and is valid for 7 days. Only the token's SHA-256 is stored. There is one
pending invitation per email, and an expired one is replaced. Resend rotates
the token. Accepting requires a logged-in, verified account whose email
matches, so the token alone authorizes nothing. It also rechecks that the
inviter is still the owner. People without an account register and verify
first. Accepting never creates an account or a second membership. Rate
limits: 30 invitations and 10 resends per user per hour
(`VENDOR_RATE_LIMITS`).

**Ownership transfer:** the owner picks an existing active, verified member.
In one transaction the old owner becomes MEMBER and the target becomes OWNER.
The owner can't leave or be removed until they transfer. All
membership-changing operations lock the business row, so concurrent
transfers, leaves and acceptances can't leave zero or two owners.

**Future market applications** will reference one `VendorBusiness` (plus the
account that submitted) and keep a snapshot of the profile at submission
time, so later profile edits don't rewrite past applications. Deleting or
archiving a business isn't supported until that interaction is designed.

**Frontend:** no Next.js screens exist yet for vendor profiles or for the
`/vendor-invitations/accept` link. Until the frontend-integration phase, use
the API docs page (`/api/v1/docs`) or the curl helpers under [Try it with curl](#try-it-with-curl).

## Organization restrictions (moderation)

An organization can **restrict** an account or a vendor business from
taking part in *its* markets. A restriction only affects participation in
that one organization. It never:
- deactivates the account or blocks login;
- changes or deletes the vendor business;
- removes any membership, or touches other organizations.

Team access is also untouched: a restricted STAFF member keeps their team
role, and restrictions aren't used for staff suspension.

| Target | Blocks |
| --- | --- |
| `account` | Participation initiated by that person, whichever business they act for. Their businesses and colleagues aren't restricted. |
| `vendor_business` | Participation for that business, whichever member acts for it. |

An action is refused if **either** the acting account **or** the selected
business has an effective restriction in that organization.

**Who can moderate:** the organization's OWNER and ADMIN can create, list,
view and revoke restrictions, checked against their current membership on
every request. STAFF get 403 and non-members 404. Reasons, notes and history
are visible only to them. The moderator, organization and timestamps always
come from the server, never from the request.

| Endpoint (under `/api/v1/organizations/{id}`) | Body / query | Success |
| --- | --- | --- |
| `POST /restrictions` | `{"account_id": 7, "reason": "…", "expires_at": "2026-12-31T23:59:00Z"}` or `{"vendor_business_id": 3, "reason": "…"}` | 201 restriction |
| `GET /restrictions` | `?status=effective\|expired\|revoked&target_type=account\|vendor_business&cursor&limit` | 200 `{"items", "next_cursor"}` |
| `GET /restrictions/{restriction_id}` | — | 200 restriction |
| `POST /restrictions/{restriction_id}/revoke` | `{"note": "optional"}` | 200 restriction |

A restriction looks like:

```json
{"id": 1, "target_type": "account", "account_id": 7, "vendor_business_id": null,
 "status": "effective", "reason": "Repeated no-shows", "created_by_user_id": 2,
 "created_at": "…", "expires_at": null, "revoked_at": null,
 "revoked_by_user_id": null, "revocation_note": ""}
```

Targets are given by stable id. There is deliberately no user search or
vendor directory; future organizer screens will take ids from applications
and bookings. Validation codes:
- `target_invalid`: give exactly one target;
- `target_not_found`;
- `reason_invalid`: the reason must be 1–1000 characters;
- `expires_at_invalid`: the expiry must include a timezone and be in the future.

**Lifecycle:** status is worked out whenever it's read, with no background
job:
- `revoked` if it was revoked;
- else `expired` once `expires_at` has passed;
- else `effective`.

Records are never edited or deleted. To change a reason or duration, revoke
it and create a new one. The rules:
- **A second effective restriction for the same target** gets
  `409 already_restricted`, with the existing id in `details`. Creation locks
  the organization row, so concurrent requests can't create duplicates.
- **Revoking twice** gets `409 already_revoked`, and the first revocation's
  time, moderator and note are kept.
- **Revoking an already expired restriction** gets `409 restriction_expired`.

Creation and revocation also write an `OrganizationAuditEvent` that holds
target ids only, never the reason or note.

**Participation policy:** `moderation.policy.ensure_can_participate(organization_id, account=…, vendor_business=…)`
raises `ParticipationRestricted`, which the API returns as
`403 participation_restricted` with a generic message. It never reveals the
reason or which record matched. It doesn't check whether the account may act
for the business; callers do that first with `vendors.permissions.membership_for`.
**No Django endpoint calls it yet**, because the backend has no participation
flows so far. It must be called inside the transaction of:
- submitting a market application (Phase 10);
- creating a reservation or booking (Phases 11–12);
- starting a payment for either (Phase 13).

It must **not** block reading your own records, cancellations or refunds.
Restricting someone doesn't cancel their existing applications, bookings or
payments; those phases will define that explicitly.

**Legacy app:** the existing Next.js/Prisma application, bookings and payment
flows don't know about these restrictions and aren't enforced until they
move to Django (Phase 20). There are no moderation screens in the frontend
yet, and no notification emails or appeals.

**Try it locally:** as an organization OWNER/ADMIN, using the `post` helper
from [Organizations and teams](#organizations-and-teams):

```bash
post $O/1/restrictions '{"vendor_business_id": 1, "reason": "Late setup three weeks running"}'
curl -s -b $J "$O/1/restrictions?status=effective"; echo
post $O/1/restrictions/1/revoke '{"note": "Resolved with the vendor"}'
```

## Markets and event dates

A **market** is an organization's ongoing farmers market or popup. It has a
name, description, type (`FARMERS_MARKET` or `POPUP`), venue name, address
(`address_line1`, `address_line2`, `city`, `region`, `postal_code`, and a
two-letter uppercase `country`), optional `latitude`/`longitude`, and an IANA
`timezone`. Coordinates are sent together or not at all, and must be within
-90..90 and -180..180. They're never guessed or geocoded. An **event date**
(`EventOccurrence`) is one scheduled event of a market. Later phases
(applications, stalls, bookings) will point at event dates, so they're never
deleted.

**Status and lifecycle**

| Status | Who sees it | Rules |
| --- | --- | --- |
| `DRAFT` | Organization members only | Freely editable. |
| `PUBLISHED` | Anyone, via `/api/v1/public/...` | To publish, a market needs `name`, `venue_name`, `address_line1`, `city`, `country`, `timezone` **and** at least one upcoming scheduled date. While published, the venue fields can't be blanked (enforced by a database CHECK). Publishing twice is a no-op. |
| `ARCHIVED` | Nobody publicly (public endpoints return 404) | Final: no edits, dates or publishing. All records and dates are kept. |

Coordinates aren't required to publish. Markets without them just won't get
map markers in discovery (Phase 9).

**Event dates**
- Start and end are instants with a UTC offset (`2026-10-03T08:00:00-05:00`).
  Times without an offset are rejected, and `ends_at` must be after
  `starts_at`.
- A market can't have two dates with the same start (a unique constraint).
  Overlapping dates are allowed.
- Responses give UTC instants plus `local_date`, `local_start_time` and
  `local_end_time` in the market's `timezone`.
- **Cancel** keeps the record (`status: CANCELLED`, with an optional public
  message) and can't be undone. Cancelled dates can't be edited. Cancelling
  doesn't touch any applications, bookings or payments; none exist yet.
- The market's timezone is locked once any date exists, because changing it
  would shift every date's local time.

**Weekly recurrence** (`POST …/series`):

```json
{"frequency": "WEEKLY", "interval_weeks": 1, "weekdays": [3, 6],
 "start_date": "2026-10-01", "end_date": "2026-12-31",
 "local_start_time": "08:00", "local_end_time": "12:30"}
```

- **Supported:** weekly only; every 1–12 weeks; weekdays as ISO numbers
  (1 = Monday … 7 = Sunday); inclusive start and end dates.
- **Limits:** `start_date` not in the past (in the market's timezone); a
  span under 366 days; at most **200 dates**; and the end time after the
  start time on the same day, so overnight events must be created one by one.
- Anything else (daily, monthly, extra fields) is rejected with `422` or
  `400 recurrence_*`. Nothing is approximated.
- **Weeks are counted** from the Monday-based week containing `start_date`.
  With an interval of 2, that week, the week two later, and so on.
- **Daylight saving:** local times are kept, so 8:00 stays 8:00 across a
  change. If the start or end time falls in a skipped or repeated hour on
  any date (e.g. 2:30 on a spring-forward day), the whole request is refused
  with `400 recurrence_dst_conflict`, listing the dates. Nothing is shifted.
- **Generation:** it runs in one transaction, locking the market row, so
  concurrent requests run one at a time.
- **Idempotent:** sending exactly the same definition again returns `200`
  with `already_existed: true` and creates nothing.
- **Your edits are kept:** each generated date remembers its original slot,
  so a date you moved or cancelled is never recreated or reset.
- **Clashes:** if any generated start clashes with an existing date that isn't
  that series' own, the request gets `409 occurrence_conflict` listing the
  clashes, and nothing is created.
- **Series editing** ("edit all future dates") isn't supported yet. Edit or
  cancel individual dates instead.

**Permissions:** OWNER and ADMIN create and edit everything. STAFF can read
markets, dates and series. Non-members get 404. The organization always
comes from the URL and the caller's membership, never from the request body.
Moderation restrictions don't affect browsing.

| Endpoint (under `/api/v1/organizations/{id}`) | Body / query | Success |
| --- | --- | --- |
| `GET /markets` | `?status&cursor&limit` | 200 `{"items", "next_cursor"}` |
| `POST /markets` | market fields (`name`, `market_type`, `timezone` required) | 201 market |
| `GET` / `PATCH /markets/{market_id}` | any market fields | 200 market |
| `POST /markets/{market_id}/publish` | — | 200 (400 `publication_requirements` with `details`) |
| `POST /markets/{market_id}/archive` | — | 200 |
| `GET /markets/{market_id}/occurrences` | `?series_id&cursor&limit` (cursor = last `starts_at`) | 200 |
| `POST /markets/{market_id}/occurrences` | `{"starts_at", "ends_at"}` | 201 |
| `PATCH /markets/{market_id}/occurrences/{occurrence_id}` | `{"starts_at"?, "ends_at"?}` | 200 |
| `POST /markets/{market_id}/occurrences/{occurrence_id}/cancel` | `{"message"?}` | 200 |
| `POST /markets/{market_id}/series` | series definition | 201 new, 200 existing |
| `GET /markets/{market_id}/series/{series_id}` | — | 200 series metadata + `occurrence_count` |

**Public, no login:**

| Endpoint (under `/api/v1/public`) | Returns |
| --- | --- |
| `GET /markets/{market_id}` | Published market: venue, address, coordinates, timezone, `organizer.name`. No organization ids, contacts or status. |
| `GET /markets/{market_id}/occurrences` | Dates that haven't ended yet, in start order, **including cancelled ones** with their message. Paginated by `starts_at`. |
| `GET /occurrences/{occurrence_id}` | One date plus its public market. |

Drafts and archived markets, and their dates, return `404` publicly.

**Next phases:** Applications (Phase 10) and stalls and bookings (Phases
11–12) will reference `EventOccurrence`, and must call the moderation
participation policy.

## Market discovery

Public, no login, read-only (`markets/discovery.py`). Only **published**
markets with at least one **upcoming scheduled** date are listed. A date counts
while it hasn't ended, and cancelled dates never make a market eligible.

| Endpoint (under `/api/v1/public`) | Returns |
| --- | --- |
| `GET /markets` | `{items, next_cursor}`. Each item has the market summary, `next_occurrence`, `upcoming_preview` (the next 3 scheduled dates, including the next one), and `distance_km` (nearby searches only, otherwise `null`). Default 20 per page, max 50 (`limit`); `cursor` is opaque. |
| `GET /markets/map` | `{items, total, truncated, limit}`: markers (id, name, city, region, coordinates, next date) for markets **with coordinates** in an area. Needs a bounding box or a nearby search. At most 300 markers; `truncated` says there were more, and `total` counts them all. |

**Filters** (both endpoints, all optional and combined with AND):

- `q`: case-insensitive text in the name, venue, city or region (max 100 characters).
- `market_type`: `FARMERS_MARKET` or `POPUP`.
- `date_from` / `date_to` (`YYYY-MM-DD`, inclusive): a market matches when it has
  a scheduled, not-yet-ended date whose **start falls on those days in the
  market's own timezone**. The span is at most 366 days, and `date_to` can't be
  before `date_from`.
- `south`, `west`, `north`, `east`: a bounding box in degrees, all four
  together. `west > east` means the box crosses the antimeridian.
- `lat`, `lng`, `radius_km`: nearby search. **Units are kilometres**, 0 < radius ≤ 500.
  Distance is great-circle (haversine) from the given point, and results are
  ordered nearest first.

Without a nearby search, results are ordered by the soonest next date. Markets
without coordinates appear in the list (unless filtered by area or distance)
but never on the map, so **list and map counts can differ**. Invalid input
returns `400` with codes `query_invalid`, `date_range_invalid`, `bbox_invalid`,
`coordinates_invalid`, `near_invalid` or `radius_invalid`.

**Query approach:** plain PostgreSQL, **no PostGIS**. Distance is a haversine
SQL expression over the `latitude`/`longitude` decimals. A degree bounding box
is applied first, so it can use the partial index
`markets_published_coords_idx` on published markets' coordinates (migration
`markets.0002`). Each market's next date comes from a correlated subquery, and
its preview dates from one window query per page. This fits the expected
number of markets. If it grows past tens of thousands, move to PostGIS or a
search service, with a documented deployment change.

**Demo data (local only):** `uv run python manage.py seed_demo_markets` creates
a demo organizer and 5 published markets with weekly dates (one without
coordinates). It refuses to run unless `DEBUG` is on. **Never run it against
production.**

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

Stop `runserver` with Ctrl+C. PostgreSQL can keep running. To stop it:
`brew services stop postgresql@16` (or `pg_ctl -D /opt/homebrew/var/postgresql@16 stop`).
Your data stays. With the optional Docker setup, `docker compose stop` keeps the
data too; only `docker compose down -v` deletes it.

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
  organizations/           organizations, memberships, invitations, team audit
    permissions.py         role rules + membership lookup/locking
    services.py            create, team management, invitations, ownership
    api.py, schemas.py     /api/v1/organizations and /api/v1/invitations
  markets/                 markets, event dates, weekly recurrence, public reads
    recurrence.py          pure weekly-schedule + daylight-saving logic
    public.py              what anonymous visitors may see
  moderation/              organization restrictions + participation policy
    policy.py              ensure_can_participate() for future entry points
  vendors/                 vendor businesses, memberships, invitations
    permissions.py         owner/member rules + membership lookup/locking
    services.py            profile, members, invitations, ownership
    api.py, schemas.py     /api/v1/vendors and /api/v1/vendor-invitations
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

The public discovery pages (`/markets`, Phase 9) are the first Next.js screens
backed by Django. Setting `DJANGO_API_ORIGIN` enables the `/api/v1` forwarding
rule in `next.config.ts`; see the frontend README. Still to do: the
sign-up/verify/login/reset screens that call these endpoints, and moving each
workflow off Auth.js/Prisma.
