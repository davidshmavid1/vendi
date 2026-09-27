# Vendi

This guide describes the existing Next.js application. Run all commands below
from `frontend/`. Its `.env`, Prisma schema, scripts, and package files are local
to that directory. The new Django backend is documented in
[backend/README.md](../backend/README.md).

Multi-tenant vendor booking & event management for popup market organizers. Each
organizer runs their own market — events, spaces, vendor applications, bookings,
payments — fully isolated from every other organizer.

Three separate money flows:

1. **Vendor → organizer, for a stall.** Destination charge; the platform takes
   a small cut (`Organization.applicationFeeBps`, default 1%).
2. **Organizer → Vendi, monthly subscription.** Stripe Billing/Checkout, not
   Connect — organizers are Vendi's own paying customers. Tracked, not yet
   gating dashboard access.
3. **Vendor → organizer, application fee.** Paid once, when applying — the
   platform takes **no cut** of this one, by design (`Organization.vendorApplicationFee`,
   org-wide, editable in Settings → Application fee; 0 means no fee).

This is the **Phase 1** thin slice: multi-tenant auth + org signup → create event
+ spaces → vendor applies (optionally paying an application fee) → organizer
approves → vendor pays for a space via Stripe Connect → booking confirmed +
inventory decrements. No waitlist automation yet — see [Roadmap](#roadmap).

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
  self-serve Connect onboarding), [`src/domain/payments.ts`](src/domain/payments.ts)
  (stall booking PaymentIntent), [`src/domain/applicationFeePayments.ts`](src/domain/applicationFeePayments.ts)
  (application fee PaymentIntent, `application_fee_amount: 0`),
  [`src/domain/subscriptions.ts`](src/domain/subscriptions.ts) (organizer
  subscription via Checkout — a different Stripe product, Billing not Connect,
  with its own Customer object (`Organization.stripeCustomerId`) separate from
  the Connect account), and
  [`src/app/api/webhooks/stripe/route.ts`](src/app/api/webhooks/stripe/route.ts)
  (signature-verified webhook — the only source of payment truth; looks up the
  `Payment` row's `purpose` field to route `payment_intent.succeeded` to the
  right confirm function, and mirrors Stripe's own subscription status onto
  `Organization` for `checkout.session.completed`/`customer.subscription.*`).
- `Payment.purpose` (`BOOKING_FEE` | `APPLICATION_FEE`) is how one webhook
  handler and one `Payment` table serve both one-time payment flows without
  duplicating the confirm-payment machinery — `bookingId`/`applicationId` are
  both nullable, exactly one is set per row, enforced in the domain layer.
  `Application` gained a `PENDING_FEE_PAYMENT` status ahead of `SUBMITTED`,
  mirroring `Booking`'s `PENDING_PAYMENT` → `CONFIRMED` exactly — skipped
  entirely when `vendorApplicationFee` is 0.

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
book a space or pay an application fee — both are blocked with a clear error
until that's done.

**Local Stripe webhooks:** Stripe can't reach `localhost` directly, so run the
[Stripe CLI](https://docs.stripe.com/stripe-cli) alongside `npm run dev`:

```bash
stripe login   # once, opens a browser to link your Stripe account
stripe listen --forward-to localhost:3000/api/webhooks/stripe
```

Copy the `whsec_...` it prints into `STRIPE_WEBHOOK_SECRET` in `.env` and
restart `npm run dev`.

**One-time subscription setup:** create the $20/mo Product + Price
(`node scripts/create-subscription-price.mjs`) and put the resulting Price ID
in `STRIPE_SUBSCRIPTION_PRICE_ID`.

**Testing Connect-gated flows without the hosted onboarding UI:** Stripe's
real onboarding form renders inside a cross-origin iframe (with a CAPTCHA on
some steps), which isn't automatable and shouldn't be. For local testing,
`node scripts/create-test-connect-account.mjs` creates a fully-verified
test-mode Connect account directly via the API (Stripe's documented test
values — see [`docs.stripe.com/connect/testing`](https://docs.stripe.com/connect/testing)),
and `node scripts/link-test-connect-account.mjs <orgSlug> <acct_id>` points an
org at it. The app itself always uses real Express accounts + hosted
onboarding for actual organizers — this is a test fixture only.

**Note on migrations:** `prisma/migrations/*_init/migration.sql` is the baseline
schema, generated via `prisma migrate diff` (no live DB needed for that). Against
a real Postgres instance, `prisma migrate dev`/`migrate deploy` work normally. In
this sandbox, `prisma migrate dev`'s shadow-database step hit a wire-protocol
issue specific to the bundled `prisma dev` local server, so schema changes here
were validated with `prisma db push` instead — that's a quirk of this one local
dev server, not of the schema or the migration file.

## Market discovery (`/markets`)

Public pages for finding markets, with no login. They read from the Django
backend's public discovery API, not from Prisma.

- `/markets`: text search, market type, date range, "Use my location" with a
  radius in km, a paginated list, and a map. Filters live in the URL (`q`,
  `type`, `from`, `to`, and `area` after "Search this area"), so a view can be
  refreshed or shared.
- `/markets/[marketId]`: venue, address, organizer, description and upcoming
  dates, including cancelled ones.

Your device location is used only after you click "Use my location". It stays
in page state, is sent to Django only as a search, and is never put in the URL
or stored. On phones, a List/Map toggle switches views.

**Configuration** (server-side environment variables):

| Variable | Purpose |
| --- | --- |
| `DJANGO_API_ORIGIN` | Django origin, e.g. `http://localhost:8000`. Server pages read it at request time. `next.config.ts` forwards browser `/api/v1/*` requests to it, and that rule is fixed **at build time**, so set it for the build too (on Vercel, for the environment being built). Without it, `/markets` says discovery isn't available. |
| `MAP_TILE_URL` | Read at request time. Raster tile URL template, e.g. `https://tiles.example.com/{z}/{x}/{y}.png`. It's sent to the browser, so use a key restricted to your domain if the provider needs one. Never put a secret here. |
| `MAP_TILE_ATTRIBUTION` | Attribution text your tile provider requires, shown on the map. |

Without `MAP_TILE_URL`, or when tiles fail to load, the page falls back to the
list with a notice. Maps use Leaflet; there are no provider accounts and no
paid services. **Choose a tile provider and follow its usage terms and
attribution rules.** The public OpenStreetMap tile servers
(`tile.openstreetmap.org`) are fine for trying it locally, but their
[tile usage policy](https://operations.osmfoundation.org/policies/tiles/) does
not allow production traffic from an app like this. Production needs a
provider or self-hosted tiles chosen by the team.

**Try it locally:**

```bash
# backend/ (DEBUG development settings only; never production)
uv run python manage.py seed_demo_markets
uv run python manage.py runserver 8000
```

```bash
# frontend/ (.env)
DJANGO_API_ORIGIN="http://localhost:8000"
MAP_TILE_URL="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
MAP_TILE_ATTRIBUTION="© OpenStreetMap contributors"
```

Then run `npm run dev` and open <http://localhost:3000/markets>. `npm test`
runs the discovery helpers' unit tests (`src/lib/discovery/logic.test.ts`).

## Vendor applications (Django-backed)

The second set of screens backed by Django (Phase 10). They don't use Prisma
or the legacy Auth.js session.

| Route | For |
| --- | --- |
| `/account/login`, `/account/register`, `/verify-email` | Django account sign-in, sign-up, and the email confirmation link. The Django session is separate from the legacy `/login` (Auth.js), and neither trusts the other. |
| `/markets/[marketId]` | Each date shows **Apply as a vendor** while its applications are open, or when they open. |
| `/markets/[marketId]/dates/[occurrenceId]/apply` | The application form. You pick which of your businesses applies; only a business owner can apply. Shows closed, not-open-yet, already-applied, restricted and changed-questions states. |
| `/vendor/businesses/new` | A minimal form to create your own vendor business, used when you have none. It isn't linked to any organizer. |
| `/vendor/applications`, `/vendor/businesses/[id]/applications/[id]` | Your businesses' applications and their status, answers and organizer message. Owners can withdraw a submitted application. |
| `/organizer`, `/organizer/[orgId]/applications[/id]` | Organizer list, filterable by status, and the review page. Owners and admins approve or decline with an optional message; staff can view. |

**How it talks to Django:**
- Client components call `/api/v1/...` on this origin with `credentials: "same-origin"` (`src/lib/django/client.ts`).
- The CSRF token comes from `/api/v1/auth/csrf` and is kept in memory only. It's refreshed after login and logout, and once more on a `csrf_failed` response.
- All authorization is decided by Django; the pages only reflect it.
- Submit buttons disable while a request is in flight, and the server also rejects duplicates.
- Approval is shown as being accepted for a date, never as a stall reservation or a completed booking.

**Try it locally:** run `uv run python manage.py seed_demo_applications` in
`backend/` (DEBUG only). Sign in at `/account/login` as `demo-vendor@example.com`
to apply, or as `demo-organizer@example.com` to review. Both use the password
in `seed_demo_markets`. New accounts get their confirmation link in the Django
runserver console during development.

**Not built yet:** password reset and change screens, and an organizer screen
for intake settings. Organizers configure intake through the API for now.

## Stall layouts (Django-backed)

Phase 11 adds per-date stall layouts.

- **Organizer editor:** `/organizer` → **Stall layouts** → market → date → `/organizer/[orgId]/markets/[marketId]/dates/[occurrenceId]/layout`.
  - Set the canvas size and currency, add rectangular stalls, then drag them or move them with the arrow keys (Shift = 10 units).
  - Every value can be typed in the stall panel: label, description, position, size, real size and unit, price, and offered/disabled. None of it needs a pointer.
  - Problems (overlaps, off-canvas stalls, duplicate labels, bad prices) show immediately and block saving; the server validates everything again.
  - Saving sends the revision you loaded. If someone else saved first, you get a message and a **Load the latest version** option, which asks before discarding your edits.
  - Unsaved changes are flagged, and discarding or leaving asks first.
  - Publish makes the layout visible to vendors. A published layout is read-only until you unpublish it.
- **Public view:** `/markets/[marketId]/dates/[occurrenceId]` (linked as "Date details and stalls") shows:
  - the published plan, a stall detail panel ("View stall details"), and an equivalent table for keyboard and screen-reader users;
  - disabled stalls as "Not offered", with a note that prices exclude taxes or fees and listed stalls aren't reserved;
  - no Reserve or Pay actions yet.
- **Money:** prices are integer minor units from the API, formatted with the layout's `currency_exponent` (`src/lib/layouts/money.ts`), for example `$25.00` or `¥2,500`. Typed prices are parsed with string arithmetic, never floats.

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
