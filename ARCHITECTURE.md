# Architecture

This is the internal reference for how Vendi is put together and the rules we
follow so it doesn't get messy as it grows. `README.md` covers stack/setup;
this doc covers structure and conventions.

## Stack

Next.js 16 (App Router) + React 19 + TypeScript, Prisma 7 (driver-adapter model)
over PostgreSQL, Auth.js v5 (Credentials + JWT), Stripe Connect. No separate
backend service — Server Actions and Route Handlers *are* the backend.

## The five layers

```
Pages & Components        src/app/**, src/components/**
        |  form submit
Server Actions & Routes    src/server/actions/**, src/app/api/**
        |  calls one domain function
Domain layer               src/domain/**
        |  prisma.model.update(...)
Prisma Client              src/lib/prisma.ts
        |  SQL
PostgreSQL
```

**Authorization** (`src/lib/authz.ts`) is not a layer in this stack — it's a
cross-cutting check consulted by both the Actions layer (`requireOrgContext`,
`requireAdminContext`) and the Domain layer (`requireRole`, `requireVendor`
inside state machine functions). It gets checked twice on purpose: once at the
boundary where a request enters the server, once again inside the business
logic that's about to mutate something. Neither check is optional or a stand-in
for the other.

### Rules per layer

- **Pages & Components** never import `@/lib/prisma` or call Stripe directly.
  They render what a Server Component already fetched, or submit a form to a
  Server Action. If a component needs new data, that's a sign a Server
  Component needs to fetch it — not that the component should reach around the
  layers below it.
- **Server Actions & Route Handlers** stay thin: call `requireOrgContext`/
  `requireAdminContext` → validate input (zod) → call *one* domain function →
  `revalidatePath`/redirect. If an action file is doing real logic — an
  `if (status === ...)`, a loop, a calculation — that logic belongs in
  `src/domain/` instead.
- **Domain layer** is where every business rule and state transition lives.
  Two state machines exist today (`applicationStateMachine.ts`,
  `bookingStateMachine.ts`); each has an explicit transition table
  (`Record<Status, Status[]>`) that every mutation is checked against, so a new
  workflow rule means editing one table in one file, not hunting for scattered
  conditionals. This layer is deliberately **not** object-oriented — see below.
- **Prisma Client / PostgreSQL** — no raw SQL, no query outside a domain
  function or an authorized action, and every query merges `tenantWhere(ctx)`
  or `vendorRowWhere(ctx)` from `authz.ts`. There is no code path that queries
  a tenant-scoped table without an org filter.

## Why the domain layer is functions, not classes

Prisma is a *data mapper*, not *active record* — `Organization`/`User`/`Vendor`
etc. are plain typed objects with no methods attached (contrast with Rails/
Django, where the row object itself has `.approve()`-style methods). We lean
into that on purpose: business logic is a set of standalone functions in
`src/domain/` that take plain data in and return plain data / side effects out.
This is sometimes called a "functional core."

The one deliberate exception is `EventDispatcher`
(`src/domain/events/dispatcher.ts`) — a real class, because it needs to hold
internal mutable state (a map of registered handlers) behind methods (`on`,
`emit`). That's "a class for a stateful service," which is a different, valid
reason to reach for OOP than "a class to represent a business entity," which
we don't do.

## Adding a new resource type

Same shape every time:

1. Prisma model, scoped by `organizationId` (and `vendorId` if it's
   vendor-owned).
2. `src/domain/<thing>.ts` — plain functions; a state machine file if it has a
   status that transitions.
3. `src/server/actions/<thing>.ts` — thin actions, one per workflow step.
4. Pages under `src/app/o/[orgSlug]/...`, split into organizer vs. vendor views
   as needed.

## Event-driven side effects

State machine transitions write their `AuditEvent` **inside the same
transaction** as the state change (`src/domain/audit.ts`) — that part must be
atomic, it's the source of truth for analytics. Anything that shouldn't be
atomic with the DB write (sending an email, later: queueing a waitlist notify)
goes through `eventDispatcher.emit(...)` after the transaction commits, handled
by `src/domain/events/handlers.ts`. Never call an email/notification side
effect directly from inside a state machine transition — always route it
through the dispatcher, so Phase 2's move to a real queue (BullMQ/Graphile
Worker) only means changing `emit`, not touching any handler or call site.

## Gotchas specific to this stack (found the hard way)

- **Auth.js config must be split, and both halves need the session-shaping
  callbacks.** `src/auth.config.ts` (edge-safe, no Prisma/bcrypt, used by
  `src/proxy.ts` middleware) and `src/auth.ts` (full config with the Credentials
  providers) both need the `jwt`/`session` callbacks that copy `role`,
  `organizationId`, `organizationSlug`, `vendorId` onto the token/session.
  Defining them only in `auth.ts` leaves middleware unable to see those fields
  on `auth.user` — every login looks broken (silently redirects back to login)
  even though sign-in itself succeeded.
- **Module-scope singletons aren't reliably shared across Next's dev bundler
  layers.** Server Actions, Server Components, and the `instrumentation.ts`
  hook can each get a *different instance* of a module that just does
  `export const thing = new Thing()`. `src/lib/prisma.ts` already used the fix
  (park the instance on `globalThis`); `src/domain/events/dispatcher.ts` needed
  the same treatment after handlers registered via `instrumentation.ts` turned
  out to be registered on a different `EventDispatcher` instance than the one
  Server Actions were emitting on — a silent failure with no error, just
  handlers that never ran. Any new singleton service in this codebase should
  use the same `globalThis` pattern by default.
