# TODO

Running checklist for picking this back up across sessions. Check things off
as they get done — this isn't the long-term roadmap (see README.md's
Phase 2/3 section for that), it's "what's actually next."

## Live site status: mostly working, one real gap left

`https://vendi.events` is deployed, connected to a real Neon Postgres, and
sign-in/sign-up work correctly. **The one thing that will not work yet:
Stripe payments won't confirm in production**, because there's no webhook
endpoint registered in Stripe for the live domain (confirmed via API —
`stripe.webhookEndpoints.list()` currently returns empty). This is the exact
same class of problem as "local payments don't confirm without `stripe
listen` running" — except in production there's no CLI workaround, you need
a real permanent webhook.

- [ ] **Register a real Stripe webhook** (Stripe Dashboard → Developers →
      Webhooks → Add endpoint) pointed at
      `https://vendi.events/api/webhooks/stripe`. Subscribe it to at least:
      `payment_intent.succeeded`, `payment_intent.payment_failed`,
      `checkout.session.completed`, `customer.subscription.updated`,
      `customer.subscription.deleted`.
- [ ] Copy the `whsec_...` secret that endpoint gives you (this will be
      **different** from the local `stripe listen` one — every registered
      endpoint gets its own secret) into Vercel's `STRIPE_WEBHOOK_SECRET`
      (replace whatever's there now — it's currently a leftover/incorrect
      value, harmless only because no endpoint existed to use it yet).
- [ ] Redeploy after changing that env var (same rule as always — env var
      changes need a fresh build).
- [ ] Test a real booking or application-fee payment on the live site
      end-to-end afterward to confirm it actually flips to CONFIRMED/SUBMITTED.

## What's done and verified

- [x] Repo on GitHub (private), deployed on Vercel, custom domain
      `vendi.events` connected
- [x] Cloud Postgres (Neon) created, schema migrated, seeded with sample data
- [x] Three payment flows built and verified with real (test-mode) Stripe
      calls: stall bookings (1% cut), vendor application fees (0% cut,
      org-configurable), organizer subscriptions ($20/mo via Checkout)
- [x] Fixed a real production bug: sign-in/sign-up were redirecting to
      `localhost:3000` instead of the real domain — root cause was Auth.js
      not trusting Vercel's proxy headers by default (`trustHost: true` now
      set in `src/auth.config.ts`)
- [x] Fixed Vercel's `DATABASE_URL` (was pointing at local Postgres, not Neon)
- [x] All work merged into `main` via PRs (#1 payments, #2 housekeeping,
      #3 auth fix) — `main` and `origin/main` are in sync

## Workflow notes for next session

- **Feature branches per change now** — create a branch for any code change,
  push it, open the PR yourself rather than expecting it merged automatically.
- **Review-before-write rule is in `CLAUDE.md`** — changes get shown before
  being written to disk or pushed, unless you say to proceed in auto mode.
- **Local Postgres (`npx prisma dev`) doesn't stay running permanently** —
  if you get a connection-refused/terminated error locally, run
  `npx prisma dev ls` to check, `npx prisma dev -d` to restart it. This has
  happened a few times already; it's a "just restart it" situation, not data
  loss (data survives the restart).
- **Prisma Studio doesn't work against the local dev database** (a real quirk
  in that bundled tool, not your setup) — use `node scripts/view-table.mjs
  <table>` instead for local data, or point Studio at Neon if you want the
  full GUI (`DATABASE_URL="<neon-string>" npx prisma studio`).
- **Scripts in `scripts/` that touch the database need `npx tsx`, not plain
  `node`** (e.g. `view-table.mjs`, `check-booking.mjs`) — scripts that only
  call Stripe work fine with plain `node`. Simplest rule: always use
  `npx tsx scripts/<name>.mjs` and it'll work either way.

## After the webhook is fixed

Back to local dev/learning — no rush on Phase 2.

- Phase 1 is feature-complete, expanded with the three-payment-flow business
  model, and verified end-to-end both locally and (pending the webhook fix
  above) in production.
- Phase 2 (waitlist automation, real email delivery, audit-log dashboards) —
  not started.
- Phase 3 (cross-org vendor profiles, analytics, configurable workflows) —
  not started.
