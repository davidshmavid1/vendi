# Vendi backend database

How the Django backend uses PostgreSQL: who owns which database, the
conventions new models follow, how to work with migrations, and the
**proposed** data model for later phases. Setup commands live in
[README.md](README.md). This file covers the database rules.

> **Status:** implemented Django models are `accounts.User` and the
> `organizations` models (Organization, OrganizationMembership,
> OrganizationInvitation, OrganizationAuditEvent), plus Django's session and
> cache tables. Everything else
> under [Proposed data model](#proposed-data-model) is a design proposal. None
> of those tables exist yet.

## Ownership

| Database | Owner | Migrations | Contains |
| --- | --- | --- | --- |
| Backend DB (`BACKEND_DATABASE_URL`, e.g. `vendi_backend_dev`) | Django | `backend/*/migrations/` | `accounts_user`, `django_session`, `vendi_cache` (rate limits), Django's `auth_*`/`django_*` tables |
| Existing app DB (`DATABASE_URL`, Neon in production) | Prisma (Next.js) | `frontend/prisma/migrations/` | `Organization`, `User`, `Booking`, … and `_prisma_migrations` |
| Test DB (`test_<backend db name>`) | pytest-django | created and dropped per test run | same as the backend DB |

Neither migration system manages the other's tables. Legacy data moves over
only in the Existing Data Migration phase, as an explicit, reviewed import. It
never happens by pointing Django at the Prisma database.

Safeguards (all in code, all tested):

1. The backend reads only `BACKEND_DATABASE_URL`, never `DATABASE_URL`.
2. Development and test settings reject non-local hosts unless
   `BACKEND_ALLOW_REMOTE_DATABASE=true`.
3. `migrate` refuses any database containing `_prisma_migrations`: system
   check `vendi.E001` in `core/checks.py`. This catches a local Prisma
   database that guard 2 would allow.

## Conventions

**Primary keys.** Use Django's default `BigAutoField` (a `bigint` identity).
It's simple, fast and ordered. Don't expose sequential IDs as the only public
identifier where enumeration matters (for example public market URLs); add a
separate slug or UUID field there. Existing keys aren't changed for style.

**Timestamps.** `USE_TZ=True`: every `DateTimeField` is `timestamp with time
zone` and stored in UTC. Instants (created, paid, expires) are plain
`DateTimeField`s. Markets will store an IANA timezone name (e.g.
`America/Chicago`, validated with `zoneinfo`). Event dates and recurring
schedules are defined in the market's local time plus that zone, because "every
Saturday 8am–1pm" must stay 8am across daylight-saving changes. Converting to
UTC happens per occurrence, not by storing a fixed offset.

**Money.** Store an integer amount in minor units (`PositiveBigIntegerField`,
e.g. cents) plus a `currency` (`CharField(max_length=3)`, ISO 4217, lowercase to
match Stripe). Never use floats. Use `DecimalField` only for rates such as a
fee percentage, and prefer integer basis points (`100` = 1%), as the legacy app
already does. Add `CHECK (amount >= 0)` where a negative value is meaningless.

**Foreign keys and deletion.** Choose `on_delete` on purpose:

- `PROTECT`: the default for business records (bookings, payments, audit
  events). Deleting an organization with financial history must fail loudly.
- `CASCADE`: only for rows that are purely part of their parent, such as the
  stalls of a draft layout version.
- `SET_NULL`: for optional attribution that must survive, such as the reviewing
  user on an application.

In Django 5.2, `on_delete` is carried out by Django in Python. The PostgreSQL
foreign key itself has no `ON DELETE` action, so a raw SQL delete of a
referenced row fails instead of cascading. No universal soft delete: add
explicit status or archival fields where the product needs them.

**Constraints.** If the database can enforce an invariant on its own table, put
it in `Meta.constraints`: `CheckConstraint` for single-row rules,
`UniqueConstraint` (optionally with `condition=`, a partial unique index) for
"at most one". Name them explicitly: `<app>_<model>_<rule>`, e.g.
`bookings_booking_one_active_per_offer`. Model and form validation is for
friendly error messages. The constraint is the guarantee, because it also
catches bulk updates, races and bugs.

**Indexes.** Add one for a known query or a constraint, not for every column.
Foreign keys already get an index. Name composite indexes explicitly
(`<app>_<model>_<cols>_idx`) and put the most selective equality column first,
usually `organization_id`.

**Transactions.** `ATOMIC_REQUESTS` stays off. Each operation that changes
several rows runs inside one `transaction.atomic()` in a service function, not
in the view. See [Transactions and concurrency](#transactions-and-concurrency).

## Migrations

```bash
uv run python manage.py makemigrations <app>        # generate from model changes
uv run python manage.py sqlmigrate <app> <number>   # read the SQL before applying
uv run python manage.py migrate                     # apply to the dev database
uv run python manage.py showmigrations              # what is applied
uv run python manage.py makemigrations --check --dry-run   # drift check (CI runs this)
```

Rules:

- Commit each migration with the model change that produced it. Never edit or
  delete a migration after it has been applied outside your machine; add a new
  one.
- Changes to populated tables go in additive steps: add a nullable column →
  backfill → add `NOT NULL` or a constraint in a later migration. Adding a
  constraint validates existing rows first, so check the data before adding it.
- Mark data migrations irreversible (`RunPython(forward, migrations.RunPython.noop)`
  or no reverse) only on purpose, and say so in the migration and the PR.
- Never run `migrate --fake` against a database with real data, and never
  apply migrations to a remote database from a laptop.

Current state:

| Migration | What it does | Reversal |
| --- | --- | --- |
| `accounts.0001_initial` | `accounts_user` (Phase 1) | Reversible |
| `accounts.0002_email_login` | Checks existing rows and **stops, listing user ids**, if any have an empty email or share a normalized email (nothing is deleted or merged). Then lowercases/trims emails, drops `username`, makes `email` unique, adds `email_verified_at` and the `accounts_user_email_normalized` CHECK (`email = lower(email) AND email <> ''`). | **Irreversible once accounts exist:** re-adding `username` (NOT NULL, UNIQUE) fails on existing rows. Only roll back empty development databases. |
| `core.0001_cache_table` | Creates `vendi_cache` for Django's database cache | Drops the table |
| `organizations.0001_initial` | Organization, OrganizationMembership (unique per user; partial unique index `organizations_membership_one_owner` = at most one OWNER), OrganizationInvitation (unique token digest; partial unique index for one PENDING invite per email; role ≠ OWNER; normalized email), OrganizationAuditEvent | Reversible on an empty database. Rolling back drops the tables **and their data**. |

Foreign keys to users and organizations from these tables are `PROTECT`:
deleting a user who is a member (or an organization with members) fails
instead of removing the team.

## Tests

`uv run pytest`. pytest-django creates `test_<db name>` on the same PostgreSQL
server, runs every migration, and drops it at the end. Tests marked
`@pytest.mark.django_db` run inside a transaction that is rolled back.
PostgreSQL DDL is transactional too, so even a test that creates a table leaves
nothing behind. Constraint tests should write with `Model.objects.create()` or
`.update()` and expect `IntegrityError`, which proves the database (not
validation code) rejects the bad row.

## Inspecting with psql

```bash
psql "$BACKEND_DATABASE_URL"            # from backend/, after: set -a; source .env; set +a
```

```sql
\dt                                  -- tables
\d accounts_user                     -- columns, indexes, constraints of one table
\di                                  -- all indexes
SELECT conname, pg_get_constraintdef(oid)
  FROM pg_constraint WHERE conrelid = 'accounts_user'::regclass;
SELECT app, name, applied FROM django_migrations ORDER BY applied;
```

## Transactions and concurrency

A **transaction** makes several statements succeed or fail together. It does
not, by itself, stop two requests from acting on the same stale data. At
PostgreSQL's default `READ COMMITTED` isolation, two transactions can both
read "1 stall left" and both write. Patterns for booking and payment work:

1. **Keep transactions short and local.** `with transaction.atomic():` around
   database work only. No Stripe, email or other HTTP calls inside: they are
   slow, hold locks, and cannot be rolled back.
2. **Make competing writes conditional.** Either
   - a conditional update whose result you check:
     `Offer.objects.filter(pk=pk, available__gt=0).update(available=F("available") - 1)`
     returns the number of rows changed (0 means you lost), or
   - lock first, then decide:
     `Booking.objects.select_for_update().get(pk=pk)`, then check the status,
     then write. Only one transaction can hold that row lock at a time; the
     others wait and then see the committed state.
3. **Let uniqueness have the final say.** Idempotency keys, one active
   reservation per offer and one booking per payment are `UniqueConstraint`s.
   Catch `IntegrityError` inside a nested `transaction.atomic()` (a savepoint)
   and turn it into a domain result such as "already booked".
4. **Lock in a consistent order.** When locking several rows, always lock in
   the same order (for example by primary key, or offer before booking) so two
   transactions can't deadlock waiting on each other.
5. **Retry only what is retryable.** Deadlocks (`40P01`) and serialization
   failures (`40001`) may be retried a bounded number of times with the whole
   transaction re-run. Constraint violations are business outcomes, not
   retries.
6. **External effects happen after commit, durably.** PostgreSQL cannot undo a
   Stripe charge. Money flows need their own state (`PaymentAttempt`) and
   compensating actions (a refund) when the database side fails.
   `transaction.on_commit()` runs a callback after commit, but it is in-memory:
   if the process dies, the work is lost. Durable notification and payment
   follow-ups need a transactional outbox plus a worker (Background Workers
   phase).

### Weaknesses in the legacy booking flow these rules address

Found in `frontend/src/domain/bookingStateMachine.ts` and
`frontend/src/app/api/webhooks/stripe/route.ts`. The legacy app is not changed in this
phase; these are the problems the Django design must not repeat.

| Legacy behavior | Consequence | Rule |
| --- | --- | --- |
| `createBooking` checks `availableQty > 0` but doesn't reserve; inventory only drops when the webhook confirms payment. | Several vendors can pay for the last stall. The loser's charge succeeds, then `confirmBookingPayment` throws "sold out", the transaction rolls back, and the webhook returns 500, so Stripe retries indefinitely while the vendor has been charged with no booking and no refund. | Reserve inventory *before* taking payment (`Reservation` with expiry). Compensate with a refund when confirmation is impossible (6). |
| `confirmBookingPayment` reads `payment.status` without a lock to detect duplicates, then updates unconditionally. | Two concurrent deliveries of the same webhook can both see `PENDING` and both run the conditional decrement, removing two units for one booking and writing duplicate audit events. | Lock the payment/booking row (`select_for_update`) or make the status change itself conditional (2), plus a unique processed-event key (3). |
| `cancelBooking` and `confirmBookingPayment` both read the booking status, then write it, without locking. | A cancel racing a confirmation can leave a `CANCELED` booking with inventory decremented, or a confirmed booking whose cancel restored inventory. | Lock the booking first, then check the transition (2), with consistent lock order (4). |
| Cancel sets `availableQty + 1` and `status = AVAILABLE` without bounds. | Inventory can exceed `totalQty`, and a `CLOSED` space silently reopens. | `CHECK (0 <= available AND available <= total)` on saleable inventory. Don't overwrite unrelated states. |
| "A Payment belongs to exactly one of booking/application" is enforced only in app code. | Any other code path can create an orphaned or ambiguous payment. | `CheckConstraint` for "exactly one of" rules. |
| Side effects are emitted in-process after commit. | Lost if the process stops between commit and send. | Outbox + worker (6). |

## Tenant integrity

- **Authentication is not authorization.** Knowing who the user is says nothing
  about which organization they may act for. Every organization-scoped
  operation checks an `OrganizationMembership` (or `VendorBusinessMembership`)
  for that specific organization or business.
- **Query filters are not integrity.** Filtering by `organization_id` keeps
  reads scoped, but only the schema stops a booking from pointing at a stall
  owned by another organization.
- **Don't copy `organization_id` onto children when it can be derived.** A
  `Stall` gets its organization through `LayoutVersion → EventOccurrence →
  Market → Organization`, so there's nothing to get out of sync.
- **Where it is copied for scoping or indexing, enforce consistency.**
  PostgreSQL supports composite foreign keys: give the parent
  `UNIQUE (id, organization_id)` and the child
  `FOREIGN KEY (parent_id, organization_id) REFERENCES parent (id, organization_id)`.
  Django 5.2 can't declare composite foreign keys, so these would be added with
  a reviewed `RunSQL` migration. Use them only for relationships that cross a
  tenant boundary in a risky way (a booking to an offer, a payment to a
  booking).
- **`CHECK` constraints only see the current row.** They can't query other
  rows or tables. Rules like "the vendor holds an approved application for
  this occurrence" or "the vendor isn't banned by this organization" are
  enforced in a service function inside a transaction that locks the rows it
  relies on. Uniqueness and foreign keys backstop what they can.
- **Row-level security (RLS) is not implemented.** It stays an option once
  real multi-tenant tables exist and a concrete need (for example direct
  database access by other services) justifies it.

## Proposed data model

**Proposal only.** Names and fields will change as each phase designs them.

```
User ──< OrganizationMembership >── Organization ──< Market ──< EventOccurrence
  │                                      │                          │
  └──< VendorBusinessMembership >── VendorBusiness                  ├──< LayoutVersion ──< Stall
                                         │                          ├──< StallOffer (stall × occurrence)
                                         │                          └── ApplicationFormVersion
                                         ├──< Application (→ occurrence or market, form version, snapshot)
                                         ├──< Reservation (→ StallOffer, expires_at)
                                         ├──< Booking (→ StallOffer, Application)
                                         └──< OrganizationBan >── Organization
PaymentAttempt (→ Booking/Reservation or Application)
AuditEvent (→ Organization, actor User)
```

| Entity | Scope | Purpose and key relationships |
| --- | --- | --- |
| **User** | Global | One person. No organization, market or role on the row. |
| **Organization** ✅ implemented | Global identity | Operates markets. Owns all operational records below it. |
| **OrganizationMembership** ✅ implemented | Organization | (user, organization, role OWNER/ADMIN/STAFF). Unique per pair. Authorization comes from here. |
| **VendorBusiness** | Global | Reusable vendor identity and profile (name, category, contact). Not owned by any organization. |
| **VendorBusinessMembership** | Business | People who may act for the business. |
| **Market** | Organization; **public** listing fields | A recurring market or one-time popup. IANA timezone, location, public description. |
| **EventOccurrence** | Organization; public date/time | One specific date and its local hours. Generated from a schedule or created directly. |
| **ApplicationFormVersion** | Organization | Immutable set of questions. New edits create a new version. |
| **Application** | Organization (private) | A vendor business's submission and review state. Stores the form version and a **snapshot** of profile data and answers at submission, so later profile edits don't rewrite history. |
| **LayoutVersion / Stall** | Organization | The physical map definition: stall labels, sizes, positions. Versioned so past bookings keep their layout. |
| **StallOffer** | Organization | *Saleable inventory for one occurrence*: which stall, price (minor units + currency), eligibility, capacity. Separates "what exists" (Stall) from "what's for sale on that date". |
| **Reservation** | Organization | Temporary claim on a StallOffer with `expires_at`. Created before payment. At most one active per offer unit (partial unique constraint). |
| **Booking** | Organization | Confirmed participation. References the StallOffer and the Application that authorized it. |
| **PaymentAttempt** | Organization | One provider attempt (Stripe PaymentIntent/Checkout ID, unique), amount, currency, status. Links to what it pays for. Carries idempotency keys. |
| **OrganizationBan** | Organization | Blocks a VendorBusiness (and/or user) from one organization's markets. |
| Platform suspension | Global | Different from a ban: disables an account or business everywhere. A platform-admin action, recorded in AuditEvent. |
| **AuditEvent** (team changes implemented as `OrganizationAuditEvent`) | Organization (or platform) | Append-only: actor, action, entity, before/after state, request ID. Written in the same transaction as the change. |

Boundaries to keep:

- **Global identities vs. organization-owned records:** User and
  VendorBusiness belong to no organization. Everything operational does.
- **Public vs. private:** Market, EventOccurrence and public listing fields are
  discoverable. Applications, bookings, payments, bans and audit history are
  visible only to the owning organization and the vendor involved.
- **Layout vs. inventory:** Stall is the map; StallOffer is what's for sale on
  a date. Inventory and pricing rules live on StallOffer.
- **Mutable profile vs. history:** VendorBusiness changes over time.
  Applications keep what was submitted.
- **Organization ban vs. platform suspension:** separate tables and separate
  permissions.

## Deferred decisions

| Decision | Phase |
| --- | --- |
| Account linking and merging with legacy users (email login, uniqueness and normalization are decided: see README → Accounts). Email-address changes. | Existing Data Migration / later accounts work |
| Whether an Application targets a market season or a single occurrence. Whether one approval covers many dates. | Applications |
| Capacity model: one vendor per stall vs. quantity-based offers (legacy `Space.totalQty`). Reservation expiry length. | Event Maps / Reservations |
| Currency support: single currency per organization vs. per offer. | Payments |
| Refund and cancellation policy rules. | Cancellations & Refunds |
| Public identifiers (slug vs. UUID) for markets and organizations. | Markets |
| Whether to use composite foreign keys or derived scoping for each tenant-crossing relationship. | Each domain phase |
| Row-level security. | Revisit after domain tables exist |

Migration risk to plan for: importing legacy data means mapping Prisma
`cuid` string IDs to new `bigint` keys (keep the legacy ID in an indexed
column during the transition), splitting legacy per-organization `User` rows
into global users plus memberships, and reconciling duplicate emails across
organizations. Each needs a documented dry run and a rollback plan before it
runs.
