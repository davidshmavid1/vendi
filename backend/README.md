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

**Next phases:** stalls and bookings (Phases 11–12) will reference
`EventOccurrence`, and must call the moderation participation policy.

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

## Vendor applications

A vendor business applies to **one event date** (`EventOccurrence`); the
operating organization reviews it (`applications/`). Applying uses the shared
vendor business and never creates accounts, organization memberships or
duplicate vendor profiles. **Approval does not reserve a stall or take
payment**; those come in later phases.

**Models:**

- `ApplicationIntake`, one per date:
  - `enabled`, optional `opens_at`/`closes_at`, and vendor-facing `instructions`;
  - `questions`, with `questions_version` bumped whenever the questions change.
- `Application`, unique per (date, vendor business), a database constraint:
  - `vendor_business` (the authoritative identity) and `submitted_by`;
  - `status`, `answers`, and a **copy of the questions** as they were when it was submitted, with their version;
  - a `vendor_snapshot` (name, category, description, contact email, phone, website, city, region);
  - lifecycle timestamps, `decided_by`, and a vendor-visible `decision_message`.
- `ApplicationEvent`: append-only history of every status change, with its actor and time.

Applications are never deleted.

**Snapshots:** editing the questions later, or editing the business profile,
changes neither what an application shows as asked nor what reviewers saw.
The snapshot is a record only. It isn't editable and isn't a second profile;
`vendor_business_id` always points at the live business.

**Questions** (at most 20). Each has a stable organizer-chosen `id`
(`[a-z0-9][a-z0-9_-]{0,39}`), a `label` (max 300 characters) and `required`.

| Type | Answer |
| --- | --- |
| `short_text` | Text, max 200 characters |
| `long_text` | Text, max 2000 characters |
| `single_choice` | One of 2–20 `choices`, each max 100 characters |
| `acknowledgement` | Checkbox (`true`); a required one must be checked |

There are no conditional questions or file uploads.

**Intake rules:**
- `opens_at < closes_at` when both are set (also a DB CHECK).
- `closes_at` can't be after the event starts, and `opens_at` must be before it.
- Submissions always stop at the event's start.
- Archived markets can't be changed.

**Transitions** (all others return `409 application_not_submitted`):

| From | To | Who |
| --- | --- | --- |
| — | `SUBMITTED` | Vendor business OWNER |
| `SUBMITTED` | `APPROVED` / `REJECTED` | Organization OWNER or ADMIN |
| `SUBMITTED` | `WITHDRAWN` | Vendor business OWNER |

Decided and withdrawn applications are final. There's no resubmission,
reopening, waitlist, or withdrawal after approval yet, and a withdrawn
application still counts as the business's one application for that date.

**Permissions:**

| Action | Who |
| --- | --- |
| Configure intake | Organization OWNER, ADMIN (STAFF read) |
| List, view organization applications | Organization OWNER, ADMIN, STAFF |
| Approve, reject | Organization OWNER, ADMIN |
| Submit, withdraw | Vendor business OWNER |
| List, view the business's applications | Vendor business OWNER, MEMBER |

Vendor membership grants nothing on the organizer side, and organization
roles grant nothing on the vendor side (404, like other scoped lookups).
MEMBER can read but not submit, matching Phase 6's rule that only the owner
acts for the business.

**Submission checks**, server-side in one transaction:
- the account is logged in and verified (`email_not_verified`), and is the business's owner;
- the market is published (otherwise 404), and the date is scheduled and hasn't started;
- intake is enabled and inside its window: `409 applications_closed`, with `details: [{"state": "not_accepting" | "not_open_yet" | "closed"}]`;
- neither the account nor the business is restricted by the organization: `403 participation_restricted`, a generic message with no reason;
- `questions_version` matches, otherwise `409 questions_changed` (the form was edited while the vendor was filling it in);
- answers are valid, otherwise `400 answers_invalid`, with `details` listing `{question_id, message}`;
- no application exists yet, otherwise `409 application_exists`, with the existing id and status.

A restriction added later doesn't change existing applications, which stay
readable. Approval re-checks eligibility: a restricted account or business
gets `409 applicant_restricted`, and a cancelled, started or no-longer-public
date gets `409 occurrence_unavailable`. Rejection is still allowed in both cases.

**Concurrency:**
- Submitting and configuring lock the date (`FOR NO KEY UPDATE`), then its intake row.
- Decisions and withdrawals lock the application row, so exactly one transition wins.
- The unique constraint catches any remaining duplicate race.
- `tests/test_applications_concurrency.py` covers these cases. With the application lock removed, the race test fails.

**Endpoints** (all under `/api/v1`; mutations need the CSRF header):

| Endpoint | Body / query | Returns |
| --- | --- | --- |
| `GET /public/occurrences/{id}/application` | — | `state`, window, instructions, `questions_version`, `questions` (questions only while open or opening later) |
| `GET /public/markets/{id}/application-windows` | — | State per date that hasn't started |
| `GET` / `PUT /organizations/{org}/markets/{market}/occurrences/{occ}/application-settings` | `{enabled, opens_at?, closes_at?, instructions?, questions?}` (full replace) | Intake settings with `state` |
| `GET /organizations/{org}/applications` | `?status&market_id&occurrence_id&cursor&limit` | Summaries |
| `GET /organizations/{org}/applications/{id}` | — | Full application with `history` and actor ids |
| `POST /organizations/{org}/applications/{id}/approve` or `/reject` | `{"message"?}` | Updated application |
| `POST /vendors/{business}/applications` | `{occurrence_id, questions_version, answers}` | 201 (rate limit `APPLICATION_RATE_LIMITS`) |
| `GET /vendors/{business}/applications` | `?status&occurrence_id&cursor&limit` | The vendor's view |
| `GET /vendors/{business}/applications/{id}` | — | The vendor's view |
| `POST /vendors/{business}/applications/{id}/withdraw` | — | Updated application |

The vendor's view has no reviewer identities, actor ids or history. Status,
actors, timestamps and ownership never come from request bodies; unknown
fields return 422. There are no reviewer-only notes in this phase.

**Demo data (local only):** `uv run python manage.py seed_demo_applications`.
It runs `seed_demo_markets`, opens applications with sample questions on 4
dates per demo market, and creates `demo-vendor@example.com` with a business.
Both demo accounts use the password in `seed_demo_markets`. It needs `DEBUG`;
**never run it against production.**

## Stall layouts and pricing

Organizers draw a market's stall plan once and reuse it across dates, then set
prices per date (`layouts/`). This **defines sellable spaces only**. Holds
are in `reservations/` (see Stall reservations), and a listed stall isn't a
promise it can be booked.

**Models:**

| Model | Holds |
| --- | --- |
| `LayoutVersion` | A market's physical plan: `number` (1, 2, 3… per market), logical canvas size, `revision`, `locked_at`, `based_on` (the version it was copied from). |
| `Stall` | One rectangle of a version: stable `id`, `label`, optional `description`, canvas position and size, optional real-world size. **No price.** |
| `OccurrenceLayout` | Which version one `EventOccurrence` uses, plus that date's pricing `revision` and `published_at`. One per date. |
| `StallOffer` | What a date sells: one per `(occurrence, stall)` (DB unique), with `price_minor`, `currency` and `enabled`. |

**Rules:**
- **A version is editable only until a date uses it.** The first save of a date that selects it sets `locked_at`, which is never cleared. After that, edits return `409 layout_version_locked`.
  - To change a plan in use, copy it (`POST …/layout-versions` with `{"copy_of": id}` creates the next number with the same canvas and new stalls), edit the copy, and point the dates you choose at it.
  - Editing therefore never changes another date, or a plan a date already uses.
- **A date's offers must match its version exactly:** one offer for every stall of the selected version, no stall from another version (`400 offers_invalid`), and one currency per date.
  - When a date switches versions, offers for the old version's stalls are kept for history but ignored.
  - A version must belong to the date's market, and must have stalls (`400 layout_version_invalid`).
- Stalls are never deleted. A version save lists every existing stall by `id`, and an unknown or foreign id returns `400 stall_mismatch`. To stop selling a stall on a date, disable its offer.

**Geometry:**
- The canvas is 1–10000 logical units per side, independent of screen size.
- Stalls are integer rectangles from the top-left. They must lie inside the canvas and must not overlap; sharing an edge or corner is fine.
- Real size is separate and optional: `physical_width`, `physical_depth` and `physical_unit` (`FT` or `M`) are set together, positive, with up to 2 decimals.
- Labels are unique per version case-insensitively (a DB index on `lower(label)`); max 40 characters, no control or invisible characters.
- No polygons, images, CAD features or templates beyond copying a version.

**Money:**
- Integer **minor units** (`price_minor`), never floats. This matches the legacy `Space.price` in cents.
- An ISO 4217 `currency` from the supported set (`layouts/money.py`): USD, CAD, MXN, EUR, GBP, AUD, NZD (2 decimals), JPY, KRW (0 decimals).
- Responses include `currency_exponent`, so clients format `2500` as `$25.00` or `¥2,500`.
- `0 ≤ price_minor ≤ 1,000,000,000`.
- Prices are listed prices only: no taxes, fees or totals.

**Permissions:** organization OWNER and ADMIN create, copy and edit versions,
set a date's version and offers, and publish or unpublish. STAFF can read.
Other accounts get 404. Everything is reached through
`organizations/{org}/markets/{market}/…`, so nothing can be attached to another
market or organization.

**Revisions and atomic saves:**
- Every save sends the `expected_revision` it loaded: the version's for plan edits, the date's for offers (`null` when the date has no layout yet). A mismatch returns `409 stale_revision` with the current revision.
- The whole payload is validated before anything is written, in one transaction, so an invalid stall or offer means nothing is saved (`400 layout_invalid` or `offers_invalid`, with per-item details).
- Locks are always taken in the order market, occurrence (`FOR NO KEY UPDATE`), occurrence layout, version. A plan edit racing a date's first save either finishes first or is refused as locked; it never lands afterwards.

**Publication (per date):**
- `POST …/occurrences/{occ}/layout/publish` with `{"expected_revision": n}` needs:
  - a scheduled date that hasn't ended (`409 occurrence_unavailable`);
  - a market that isn't archived;
  - at least one enabled offer (`400 layout_empty`).
- A published date's version and offers can't change until `…/unpublish` (`409 layout_published`).

**Public read:** `GET /api/v1/public/occurrences/{id}/layout` returns only a
published layout of a published market's scheduled date that hasn't ended.
Otherwise it's a 404, including for cancelled and past dates. It returns the
canvas, currency and exponent, and each stall with `offered`, `price_minor`
and `offer_id` (both `null` when not offered). There are no revisions,
versions, actors or timestamps.

**API examples** (organizer session plus the `X-CSRFToken` header):

```bash
M="$API/organizations/1/markets/1"
# A new plan (v1)
curl -X POST "$M/layout-versions" -d '{"canvas_width": 100, "canvas_height": 60, "stalls": [
  {"label": "A1", "x": 0, "y": 0, "width": 10, "height": 10,
   "physical_width": "10", "physical_depth": "10", "physical_unit": "FT"},
  {"label": "A2", "x": 10, "y": 0, "width": 10, "height": 10}]}'
# -> 201 {"id": 4, "number": 1, "revision": 1, "locked": false, "stalls": [{"id": 11, ...}, {"id": 12, ...}]}

# Edit the draft: every stall with its id, plus the revision you loaded
curl -X PUT "$M/layout-versions/4" -d '{"expected_revision": 1, "canvas_width": 100, "canvas_height": 60, "stalls": [{"id": 11, ...}, {"id": 12, ...}, {"label": "A3", ...}]}'

# Price a date (this locks v1), then publish
curl -X PUT "$M/occurrences/7/layout" -d '{"expected_revision": null, "layout_version_id": 4, "currency": "USD",
  "offers": [{"stall_id": 11, "price_minor": 2500, "enabled": true}, {"stall_id": 12, "price_minor": 2500, "enabled": false}, ...]}'
curl -X POST "$M/occurrences/7/layout/publish" -d '{"expected_revision": 1}'

# Change the plan later: copy it, edit the copy, point other dates at it
curl -X POST "$M/layout-versions" -d '{"copy_of": 4}'   # -> v2, editable

curl "$API/public/occurrences/7/layout"
# -> {"currency": "USD", "currency_exponent": 2, "stalls": [{"id": 11, "label": "A1", "offered": true, "offer_id": 31, "price_minor": 2500, ...}]}
```

**Phase 12 boundary (reservations):**
- Reference **`StallOffer.id`**; the offer identifies both the date and the stall.
- Read the authoritative price and currency from the offer at reservation time, never from the client.
- **Snapshot** the agreed `price_minor` and `currency` on the reservation, so later price edits don't change what was agreed.
- Decide then whether a date with active reservations may be unpublished, switch versions or disable offers. Nothing references offers yet, so no protection exists here.
- Application approval doesn't assign a stall; selection and allocation rules are for later phases.

## Stall reservations

A vendor whose application for a date is approved can **hold** one of that
date's stalls for a short time (`reservations/`). A hold sets the stall aside
at the price shown. It is **not a booking** and no money moves until the
vendor pays (see Payments and bookings).

**Lifecycle** (`Reservation.status`):

```
HELD ──(expires_at passes)──> EXPIRED
  │ └──(owner releases)─────> RELEASED
  └──(confirm_hold, internal)> CONFIRMED
```

- EXPIRED, RELEASED and CONFIRMED are final. Reservations are never deleted, and a hold is never revived or extended.
- **Only HELD and CONFIRMED occupy inventory.** To switch stalls, release, then take a new hold.
- `price_minor` and `currency` are copied from the locked `StallOffer` when the hold is taken. Later price edits don't change it.
- The hold lasts `RESERVATION_HOLD_SECONDS` (default 900, 15 minutes). `expires_at` is set by the server, and responses include `server_time`, so clients count down without trusting their own clock.

**Database guarantees:**
- Partial unique indexes on `offer` and on `(vendor_business, occurrence)` where `status IN ('HELD','CONFIRMED')` mean one occupying reservation per stall per date and one per business per date.
  - The predicate doesn't depend on the clock, so a HELD row past `expires_at` still occupies the index until it is switched to EXPIRED. Every operation that could collide with it does that first (see Expiration).
- Composite foreign keys make the offer, application, business and date agree: `(offer, occurrence)` → StallOffer and `(application, occurrence, vendor_business)` → Application.
- CHECK constraints: price range, supported currency, `expires_at > created_at`, and each final state's timestamp set exactly when in that state.

**Locking:**
- Every write locks the date first (`EventOccurrence … FOR NO KEY UPDATE`, the same first lock Phase 11 layout saves take), then the offer or the reservation.
- One lock order means concurrent claims on a stall, several claims by one business, release racing a claim, and confirmation racing release or expiry are serialized without deadlocks. `tests/test_reservations_concurrency.py` runs these races on separate connections.
- If a constraint still fires (it shouldn't under the lock), the service turns it into `409 hold_conflict` rather than a 500.

**Taking a hold:** `POST /api/v1/vendors/{business}/reservations` with
`{"offer_id": …, "request_key": "…"}`. Only the business's **OWNER** can take a hold; members can view.

The server checks, under the lock:
- a verified email;
- an APPROVED application for that business and date;
- a published market, and a scheduled date that hasn't started;
- a published date layout whose selected version contains the stall, with an enabled offer;
- no organization restriction (`moderation`);
- the business holds nothing else that date (`409 hold_exists`, with the existing `reservation_id`);
- the stall is free (`409 stall_unavailable`).

It returns 201. Holds are rate-limited (`RESERVATION_RATE_LIMITS["hold_user"]`, 30/hour per account).

**Idempotency:** `request_key` (1–64 of `A–Z a–z 0–9 _ -`) is unique per account and business.
- Sending the same key for the same offer returns the original reservation with 200, in whatever state it's in now. It never re-takes or extends a hold.
- The same key for a different offer is `409 request_key_reused`.
- Clients send a fresh key per attempt and reuse it only to retry an attempt whose response was lost.

**Expiration without a job:**
- Before any claim, release or confirmation, the service switches that date's lapsed holds to EXPIRED under the date's lock (`expired_at = expires_at`).
- Reads report a lapsed HELD row as `EXPIRED` even before it's switched.
- `uv run python manage.py expire_holds` does the same for every date. It is optional housekeeping, safe to run at any time or repeatedly, and nothing depends on it.

**Release:** `POST …/reservations/{id}/release` (OWNER).
- A HELD reservation becomes RELEASED.
- Releasing an already released or expired one returns it unchanged.
- A CONFIRMED one returns `409 reservation_confirmed`.
- Another business's reservation is 404.
- Restrictions don't block viewing or releasing.

**Reading:**
- `GET …/reservations` (filters `occurrence_id`, `status`; cursor pagination) and `GET …/reservations/{id}`, for any member of the business.
- `GET /api/v1/public/occurrences/{id}/stall-availability` returns `available`, `unavailable` or `not_offered` per stall of a published layout. It never says who holds a stall.

**Layout changes while stalls are reserved** (Phase 11 operations):
- While any reservation on a date occupies a stall, saving that date with a different layout version returns `409 layout_in_use`.
- Disabling an occupied stall's offer returns `409 offer_reserved`.
- Price edits are allowed and never affect existing reservations.
- Unpublishing the layout, archiving the market, cancelling the date, or a new restriction stops new holds but leaves existing ones in place.

**Phase 13 contract: confirmation.**
- Clients can never set CONFIRMED directly. The payments domain confirms a hold only after a verified payment, or for a free stall.
- It uses `lock_reservation`, `participation_problem`, `mark_payment_pending`, `clear_payment_pending` and `confirm_locked` (see Payments and bookings). `confirm_hold(reservation_id)` is the stand-alone version of the same confirmation.
- `payment_pending` marks a hold whose checkout is open or has an unknown outcome. While it is set, the hold doesn't lapse, `expire_holds` skips it, and release returns `409 payment_in_progress`.

## Payments and bookings

A held stall becomes a **booking** after a **verified** payment through Stripe
Checkout (`payments/`, `bookings/`), or immediately if its price is 0. A
browser returning from Stripe is never proof of payment.

**Funds flow:**
- This reuses the model already approved and test-verified for the legacy app (`frontend/README.md`): Stripe Connect **destination charges**. The Checkout Session and its PaymentIntent live on Vendi's platform account.
- `payment_intent_data.transfer_data.destination` is the organizer's connected account, and `application_fee_amount` is the platform's cut: `price × application_fee_bps / 10000`, rounded down, default 100 bps = 1%, per organization, as the legacy `Organization.applicationFeeBps`.
- Compensating refunds use `reverse_transfer` and `refund_application_fee`, so the organizer and the platform both give back their share.
- **Linking accounts:** a Django organization is linked to its connected account by an operator. There is no self-serve Connect onboarding in the Django app yet:

  ```bash
  uv run python manage.py link_stripe_account <organization_id> acct_123 [--fee-bps 100]
  ```

  The command asks Stripe for the account and records whether it can receive payments: its `transfers` capability must be `active`, because destination charges transfer each payment to it. `reconcile_payments` re-checks linked accounts hourly, so an account Stripe restricts stops being offered. Until an organization is linked and ready, or while `STRIPE_SECRET_KEY` is unset, checkout returns `409 payments_unavailable`; free stalls still work.

**Records** (no card data, secrets or webhook payloads are stored):

| Model | Holds |
| --- | --- |
| `PaymentAccount` | Organization → Stripe connected account, `livemode`, `charges_enabled`, `application_fee_bps`. |
| `PaymentAttempt` | One Checkout Session for one reservation. Snapshot of amount, currency, fee and destination; its own `idempotency_key`; `checkout_session_id` and `payment_intent_id`, each unique per `livemode`. **`status`** is the payment: CREATING → OPEN → SUCCEEDED, or EXPIRED, CANCELED, FAILED. A separate **`fulfillment`** (FULFILLED/UNFULFILLED) says whether a paid attempt got its stall. `provider_calls`, `last_error` and `last_synced_at` support recovery. |
| `Refund` | Compensating refund of an UNFULFILLED payment: REQUESTED → PENDING → SUCCEEDED, or FAILED/CANCELED (operator). |
| `StripeEvent` | One row per webhook event id (unique), with `processed_at`, `attempts` and `last_error`. |
| `Booking` | One per reservation (unique). It references the reservation, offer, date, application and business (composite FK, so they must match the reservation), with the price snapshot, `payment_required`, and the payment attempt, whose own composite FK means it must be for the same reservation. |

Constraints worth knowing:
- At most one CREATING, OPEN or SUCCEEDED attempt per reservation.
- A free booking has no attempt; a paid booking has exactly one.
- At most one compensating refund per payment.

**Checkout** (`POST /vendors/{business}/reservations/{id}/checkout`, OWNER, rate-limited):
1. **One short transaction** (lock order: date → reservation → attempt):
   - The reservation must be a live HELD hold with a price above 0, whose date, market, application and restrictions still allow taking part.
   - It creates a CREATING attempt with a new idempotency key, then marks the reservation `payment_pending` and extends `expires_at` to the session's expiry.
   - An existing live attempt is returned instead, so repeated or concurrent clicks reuse one session.
2. **Stripe is called with no locks held.** Parameters come only from the stored attempt, so retries are byte-identical:
   - no `payment_method_types`: the methods enabled in the Stripe Dashboard are offered (dynamic payment methods);
   - `integration_identifier` tags Vendi's sessions in the Dashboard;
   - return URLs from `FRONTEND_BASE_URL`;
   - `expires_at` = attempt creation + `CHECKOUT_SESSION_SECONDS`.
3. **A second short transaction records the outcome:**
   - **Success:** OPEN, with the session id and URL.
   - **Definitive refusal:** FAILED; the hold is released from payment and another attempt is allowed.
   - **Unknown** (timeout, 5xx, rate limit, in-flight idempotent request): the attempt stays CREATING and the API returns `503 checkout_pending`. Repeating the request resends the same key, which returns Stripe's original session. The same happens if Stripe succeeded but saving locally failed.

**Hold and checkout expiry:**
- Stripe requires a Checkout Session to last **30 minutes to 24 hours**, so a 15-minute session isn't possible.
- Policy:
  - A hold lasts `RESERVATION_HOLD_SECONDS` (15 minutes) before checkout.
  - Starting checkout extends it to the session's expiry, `CHECKOUT_SESSION_SECONDS` = 35 minutes (five minutes of margin, because Stripe measures the 30-minute minimum from when it receives the create call, and a retried create must stay valid).
  - While `payment_pending` is set, the stall can't be lost, even after `expires_at`, until Stripe's answer is known: paid, expired, cancelled, or creation refused.
- Bounds:
  - Only one live attempt at a time.
  - At most `CHECKOUT_MAX_ATTEMPTS` (3) attempts per hold.
  - A new attempt needs a hold that is still live.
  - A session is never extended, and nothing re-extends a lapsed hold.
- **Delayed payment methods** (bank debits), if enabled in the Dashboard, complete the session unpaid while the PaymentIntent is `processing`. The attempt stays OPEN with its PaymentIntent recorded, the state is `PROCESSING`, no checkout link is offered, and the stall stays held (possibly for days) until Stripe decides:
  - `checkout.session.async_payment_succeeded` (or reconcile) books it through the usual verification;
  - `checkout.session.async_payment_failed` (PaymentIntent back to `requires_payment_method` or `canceled`) closes the attempt as FAILED (`async_payment_failed`) and frees the stall.
  - To keep checkout instant-only, turn delayed methods off in the Dashboard's payment method settings.

**Confirmation:**
- **Stripe is asked directly.** Webhooks, `reconcile_payments` and the vendor's "Check payment status" all run `sync_attempt`, which retrieves the Checkout Session and its PaymentIntent from Stripe rather than trusting event payloads or metadata.
- **Verification.** A payment counts only if all of these hold:
  - the session is `complete` and `paid`;
  - the PaymentIntent `succeeded`;
  - session id, `client_reference_id`, livemode, amount (total and received), currency, destination and application fee all match the attempt.
- **Booking.** Then, in **one transaction** (date → reservation → attempt locks):
  - the reservation must still be HELD with `payment_pending`, and its date, market, application and restrictions must still allow it;
  - if so, it becomes CONFIRMED and a Booking is created, marked SUCCEEDED + FULFILLED.
  - A duplicate or concurrent confirmation sees SUCCEEDED and does nothing.
- **Paid but unfulfilled.** If the payment is valid but can't be fulfilled (inventory lost, a late payment for a closed attempt, a restriction or cancellation meanwhile, or a verification mismatch), the attempt becomes SUCCEEDED + **UNFULFILLED**. The hold ends, and a full `Refund` is requested in the same transaction.
  - The refund is sent to Stripe after commit with its own idempotency key.
  - The vendor sees "refunding" and never "booked".
  - This is compensation only; cancellations and refund policies are Phase 14.
- **Terminal states.** SUCCEEDED is never overwritten: an older `expired` event after a payment changes nothing. Final refund states never change.

**Cancel checkout** (`POST …/checkout/cancel`, OWNER):
- Vendi first asks Stripe to expire the session.
- Only once Stripe confirms it can't be paid is the attempt CANCELED and the hold released.
- If it was paid meanwhile, the booking stands. If Stripe can't be reached, nothing is released (`503`).

**Free stalls** (`POST …/confirm-free`, OWNER):
- The same authorization, eligibility and locking apply, with no Stripe call.
- It creates one Booking with `payment_required = false` and no attempt, and is idempotent.

**Webhook:** `POST /api/v1/payments/stripe/webhook`.
- **Verification:** the `Stripe-Signature` header is checked against the **raw body** with `STRIPE_WEBHOOK_SECRET`, the platform endpoint's signing secret (5-minute tolerance). A bad signature returns `400 invalid_signature` and nothing is stored.
- **CSRF:** the endpoint has no session and needs no CSRF token, because the signature authenticates it. No other endpoint's CSRF changed.
- **Recording:** each event is stored once (unique event id) and marked processed only after its effects commit. If Stripe can't be reached while processing, the endpoint returns `503`, so Stripe redelivers and the event stays retryable.
- **Ignored events** (acknowledged, recorded as ignored): events from connected accounts, events from the other mode, and sessions that aren't Vendi's.
- **Stripe setup** (test mode): add an endpoint for `https://<backend>/api/v1/payments/stripe/webhook` with events `checkout.session.completed`, `checkout.session.expired`, `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`, `refund.created`, `refund.updated` and `refund.failed`.
  - Create the endpoint with API version `2026-08-26.dahlia`, the version stripe-python 15.x (capped `<16`) uses for API calls, so event payloads and API responses match.
  - Locally: `stripe listen --forward-to localhost:8000/api/v1/payments/stripe/webhook` and use the secret it prints.
  - This endpoint is separate from the legacy Next.js one (`/api/webhooks/stripe`), and each has its own signing secret.

**Recovery:** `uv run python manage.py reconcile_payments [--limit 100]` is idempotent, bounded per category, and logs ids and error codes only. It:
- reprocesses unprocessed webhook events (lost or failed processing);
- retries CREATING attempts older than 30 seconds (interrupted checkout creation). One still unanswered 10 minutes after its session's expiry is closed as FAILED (`abandoned_…`): any session Stripe created has expired and its link never reached the vendor. This bounds how long a missing or revoked key can keep a stall pending;
- re-checks OPEN attempts that are past their session expiry, or not synced for 5 minutes (lost webhooks, payments pending local confirmation, abandoned sessions);
- sends or re-checks unresolved refunds;
- re-checks linked connected accounts not verified in the last hour;
- expires lapsed holds;
- reports refunds Stripe refused (FAILED/CANCELED), which need an operator.

No worker runs it yet: **an operator (or a cron job) must run it.** Webhooks cover the normal path. Until a schedule exists, lost webhooks, interrupted creations and refund retries wait for the next run. Every 5 minutes is a sensible schedule; overlapping runs are safe.

**Configuration:**
- `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` (environment; empty means checkout is unavailable).
  - Use a **restricted key** (`rk_…`) rather than a secret key, with write access to Checkout Sessions and Refunds, and read access to PaymentIntents and Connect accounts.
  - For development and CI, use a dedicated Stripe [sandbox](https://docs.stripe.com/sandboxes) rather than the shared test mode.
- `STRIPE_ALLOW_LIVE` (default false). **Live keys are ignored unless it is set**, so a misconfigured environment can't take real money.
- `CHECKOUT_SESSION_SECONDS`, `CHECKOUT_MAX_ATTEMPTS`, `PAYMENT_RATE_LIMITS`.

**Reads:**
- `GET …/reservations/{id}/payment` returns the authoritative state for the return page: `HOLDING`, `CHECKOUT_OPEN`, `PROCESSING`, `BOOKED`, `REFUND_PENDING`, `REFUNDED`, `REFUND_FAILED`, `EXPIRED` or `RELEASED`, plus the booking.
  - `checkout_url` is included only for the owner and only while the session can be paid.
- `POST …/payment/check` asks Stripe now (rate-limited).
- `GET /vendors/{business}/bookings[/{id}]` (members).
- `GET /organizations/{org}/bookings?market_id=&occurrence_id=` (any organization member).

**Tests:**
- `tests/test_payments.py` and `tests/test_payments_concurrency.py` replace the gateway (`payments/gateway.py`, the only module that imports `stripe`) with `tests/fake_stripe.py`. It keeps Stripe's idempotency semantics, can simulate timeouts, lost responses and refusals, and checks webhook signatures with the real verification code.
- No test calls Stripe. End-to-end test-mode verification needs `STRIPE_SECRET_KEY` (test), `STRIPE_WEBHOOK_SECRET` and a linked test connected account.

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

The public discovery pages (`/markets`, Phase 9) and the application screens
(Phase 10) are backed by Django. Setting `DJANGO_API_ORIGIN` enables the
`/api/v1` forwarding rule in `next.config.ts`; see the frontend README. Phase 10
added the Django sign-in, register and verify-email screens (`/account/*`,
`/verify-email`). They use the Django session cookie, which is independent of
the legacy Auth.js login. Still to do: password reset and change screens, and
moving each legacy workflow off Auth.js/Prisma.
