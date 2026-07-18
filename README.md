# Vendi

Multi-tenant vendor booking & event management for popup market organizers. Each
organizer runs their own market — events, spaces, vendor applications, bookings,
payments — fully isolated from every other organizer, with Stripe Connect
handling the marketplace payment split.

This is the **Phase 1** thin slice: multi-tenant auth + org signup → create event
+ spaces → vendor applies → organizer approves → vendor pays via Stripe Connect →
booking confirmed + inventory decrements. No waitlist automation yet — see
[Roadmap](#roadmap).

## Stack

- Next.js (App Router, TypeScript), React 19
- PostgreSQL via Prisma 7 (driver adapter: `@prisma/adapter-pg`)
- Auth.js (NextAuth v5) — Credentials provider, JWT sessions
- Stripe Connect (destination charges) for marketplace payments
- Tailwind v4

## Architecture

**Multi-tenancy is the outermost boundary.** Every domain table carries
`organizationId`. There is no code path that queries these tables without it.

- [`prisma/schema.prisma`](prisma/schema.prisma) — every model scoped by
  `organizationId`; `Application` and `Booking` carry explicit status enums.
- [`src/lib/authz.ts`](src/lib/authz.ts) — the one place a session becomes a
  query filter. `requireOrgContext(orgSlug)` resolves the org from the URL,
  binds it to the session (redirects if they don't match — this is the
  tenant-isolation gate), and returns an `AuthContext`. `tenantWhere(ctx)` /
  `vendorRowWhere(ctx)` are the fragments every query merges in: org-wide for
  ORGANIZER, row-level by `vendorId` for VENDOR. `requireAdminContext()` is the
  separate, explicitly cross-org path for PLATFORM_ADMIN.
- [`src/auth.config.ts`](src/auth.config.ts) + [`src/proxy.ts`](src/proxy.ts)
  (Next 16's renamed `middleware.ts`) — edge-safe coarse route/role gating
  (no Prisma/bcrypt on the Edge runtime). Precise org-match + row-level checks
  happen server-side in `authz.ts`, which always hits the database — "middleware
  + per-query authorization helper," not either alone.
- [`src/domain/applicationStateMachine.ts`](src/domain/applicationStateMachine.ts) /
  [`bookingStateMachine.ts`](src/domain/bookingStateMachine.ts) — the two state
  machines. Every transition is a guarded function here, never a scattered
  `if (status === ...)` in a route handler. `confirmBookingPayment` is the one
  function that flips a booking to `CONFIRMED`, and it's only ever called from
  the Stripe webhook — never from client-callable code — and it decrements
  `Space.availableQty` via a conditional `UPDATE ... WHERE availableQty > 0`
  inside the same transaction as the status change, so concurrent confirmations
  for the last unit of a space can't oversell it.
- [`src/domain/audit.ts`](src/domain/audit.ts) — every transition writes an
  `AuditEvent` row inside the same transaction as the state change (atomic,
  can't drift). This is the source Phase 3 dashboards will read from.
- [`src/domain/events/`](src/domain/events) — after a transition commits, it
  emits a domain event (`APPLICATION_APPROVED`, `BOOKING_CONFIRMED`, ...) to an
  in-process dispatcher; handlers in `handlers.ts` react (approval/denial
  emails today, waitlist notify hooks in Phase 2). The dispatcher's singleton
  is parked on `globalThis` — the same trick `src/lib/prisma.ts` uses — because
  Next's dev bundler runs Server Actions and the instrumentation hook in
  separate module registries, and a plain module-scope singleton silently
  wasn't shared between them. Swapping the in-process `emit` for a real queue
  (BullMQ/Graphile Worker) in Phase 2 doesn't change any handler or call site.
- [`src/server/actions/`](src/server/actions) — Server Actions for every
  workflow step. Each one calls `requireOrgContext`/`requireAdminContext` first,
  then delegates to the domain layer. No route handler talks to Prisma with an
  unscoped query.
- Stripe: [`src/domain/stripeConnect.ts`](src/domain/stripeConnect.ts) (organizer
  self-serve onboarding), [`src/domain/payments.ts`](src/domain/payments.ts)
  (destination-charge PaymentIntent with `application_fee_amount`), and
  [`src/app/api/webhooks/stripe/route.ts`](src/app/api/webhooks/stripe/route.ts)
  (signature-verified webhook — the only source of payment truth).

### Routing

```
/                                    marketing landing
/signup                              organizer self-serve signup
/admin/login, /admin                 platform admin (cross-org)
/o/[orgSlug]                         org landing (public events list)
/o/[orgSlug]/login                   org-scoped sign-in (ORGANIZER or VENDOR)
/o/[orgSlug]/vendor/signup           vendor self-serve signup
/o/[orgSlug]/dashboard/...           ORGANIZER only
/o/[orgSlug]/vendor/dashboard, ...   VENDOR only, row-scoped by vendorId
```

A vendor identity is currently scoped to one org's `Vendor` row (per the spec's
note that cross-org vendor profiles are a Phase 3 concern) — the same email can
have independent vendor accounts in different markets today.

## Setup

```bash
npm install

# Local Postgres for dev (bundles a real Postgres, no Docker needed):
npx prisma dev -d
# then create a database once:
node -e "const {Client}=require('pg');const c=new Client({connectionString:'postgresql://postgres:postgres@localhost:51214/template1?sslmode=disable'});c.connect().then(()=>c.query('CREATE DATABASE vendi')).then(()=>c.end())"

cp .env.example .env
# set DATABASE_URL to the connection string `prisma dev` printed (swap /template1 for /vendi),
# set AUTH_SECRET (npx auth secret), and Stripe test keys if you want payments to actually work

npx prisma generate
npx prisma db push        # applies the schema (see note below on `migrate dev`)
npm run db:seed           # sample org, event, spaces, vendors, applications

npm run dev
```

Seed data (`prisma/seed.ts`, password `password123` for all):

- Organizer: `organizer@maplestreet.test` → `/o/maple-street-market/login`
- Vendor (approved, ready to book): `sunny@vendor.test`
- Vendor (application pending review): `clay@vendor.test`
- Platform admin: `admin@vendi.dev` → `/admin/login`

Connect Stripe from the organizer dashboard (Settings → Stripe) before trying to
book a space — bookings are blocked with a clear error until that's done.

**Note on migrations:** `prisma/migrations/*_init/migration.sql` is the baseline
schema, generated via `prisma migrate diff` (no live DB needed for that). Against
a real Postgres instance, `prisma migrate dev`/`migrate deploy` work normally. In
this sandbox, `prisma migrate dev`'s shadow-database step hit a wire-protocol
issue specific to the bundled `prisma dev` local server, so schema changes here
were validated with `prisma db push` instead — that's a quirk of this one local
dev server, not of the schema or the migration file.

## Hard requirements, and where they're enforced

| Requirement | Where |
|---|---|
| No query without an org filter | `tenantWhere`/`vendorRowWhere` in `src/lib/authz.ts`, used by every Server Action and Server Component |
| Atomic inventory decrement | `confirmBookingPayment` in `bookingStateMachine.ts` — conditional `updateMany` inside `$transaction` |
| Payment truth from webhooks only | `confirmBookingPayment` is never imported by client-callable code, only `src/app/api/webhooks/stripe/route.ts` |
| Guard every transition | `assertLegalTransition` tables in both state machines — no transition not in the table is possible |
| Every state change audited | `writeAuditEvent` inside the same transaction as every transition |

## Roadmap

- **Phase 2** — waitlist automation with claim expiry (timers move to
  BullMQ/Redis or Graphile Worker), real email delivery, audit-log-backed
  dashboards.
- **Phase 3** — cross-org vendor profiles, analytics over the audit log,
  configurable workflows.
