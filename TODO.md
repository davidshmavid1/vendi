# TODO

Running checklist for picking this back up across sessions. Check things off
as they get done — this isn't the long-term roadmap (see README.md's
Phase 2/3 section for that), it's "what's actually next."

## Live site status: webhook registered, needs the secret in Vercel

`https://vendi.events` is deployed, connected to a real Neon Postgres, and
sign-in/sign-up work correctly. **Stripe payments still won't confirm in
production until the steps below are done** — the webhook endpoint now
exists, but Vercel is still holding the old/incorrect signing secret, so
Stripe's callbacks will fail signature verification.

- [x] **Registered the production webhook** — `we_1TzKf0AQRmwlwQ78JddDGKKb`,
      pointed at `https://vendi.events/api/webhooks/stripe`, status `enabled`,
      subscribed to exactly the five events the handler switches on:
      `payment_intent.succeeded`, `payment_intent.payment_failed`,
      `checkout.session.completed`, `customer.subscription.updated`,
      `customer.subscription.deleted`.
- [ ] Set Vercel's `STRIPE_WEBHOOK_SECRET` to that endpoint's signing secret
      (**different** from the local `stripe listen` secret — every endpoint gets
      its own). Reveal it in the Stripe Dashboard under Developers → Webhooks →
      that endpoint, or via `stripe.webhookEndpoints.retrieve()`. Deliberately
      not written down here — signing secrets don't belong in a tracked file.
- [ ] **Verify Vercel's `STRIPE_SECRET_KEY` is this same sandbox account**
      (`acct_1TsnvbAQRmwlwQ78`, key begins `sk_test_51Tsnvb…`). The webhook was
      registered in that account because that's what the local key opens; this
      could not be confirmed from outside, since Vercel masks the value and the
      publishable key isn't in the homepage bundles. **If production points at a
      different Stripe account, the endpoint above won't fire at all.** Simplest
      fix is to re-set Vercel's Stripe vars to the same sandbox values in `.env`.
- [ ] Redeploy after changing that env var (same rule as always — env var
      changes need a fresh build).
- [ ] Test a real booking or application-fee payment on the live site
      end-to-end afterward to confirm it actually flips to CONFIRMED/SUBMITTED.

## Sales tax: plumbing done, collection intentionally off

Stripe Tax is wired up end-to-end but **collects nothing yet, by design** —
there are zero tax registrations, so it calculates $0. That's deliberate:
collecting Texas sales tax without a permit isn't allowed. When the permit
arrives, adding the registration switches collection on with **no code change**.

- [x] Stripe Tax settings `active` — head office set to the Dallas, TX address;
      account defaults `tax_behavior: exclusive`, `tax_code: txcd_10103001`.
- [x] Subscription price `price_1TuzvJAQRmwlwQ78S0FumPhw` set to
      `tax_behavior: exclusive` — tax adds **on top** of $20 rather than being
      carved out, so base pricing and past earnings never need recalculating.
      **This field is permanent**; Stripe won't allow changing it on this price.
- [x] Product tagged `txcd_10103001` (SaaS – business use).
- [x] `automatic_tax` + `billing_address_collection` + `customer_update` added
      to the Checkout session in `src/domain/subscriptions.ts`. Verified against
      the sandbox: session succeeds, `amount_total` stays 2000 ($0 tax).
- [ ] **Get a Texas Sales and Use Tax Permit** (Texas Comptroller, free) before
      collecting anything.
- [ ] After the permit: add the TX registration in Stripe → tax begins
      calculating automatically. No deploy needed.
- [ ] Worth confirming with an accountant: Texas treats SaaS as a *data
      processing service* — 20% exempt, so the effective state rate is ~5%
      rather than 6.25%. Whether Vendi qualifies is a judgement call.

Note: `automatic_tax` **requires** an active head office on the Stripe account.
Enabling it without one makes Checkout fail outright (verified) — so don't
enable it in a fresh/unconfigured Stripe account without setting that first.

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

- **Now developing on a Mac (MacBook Air), not the Dell.** Node via nvm,
  Homebrew at `/opt/homebrew`, Stripe CLI + `gh` installed, `~/.zshrc` sources
  nvm and `brew shellenv`. `.env` is fully populated for local dev.
- **The Stripe account is a *sandbox*, not the main account's test mode** —
  "Vendi sandbox", `acct_1TsnvbAQRmwlwQ78`. Sandboxes are separate environments;
  if test data looks "missing", check which context the dashboard is in before
  assuming anything was lost.
- **`stripe login` isn't necessary** — `stripe listen --api-key "$STRIPE_SECRET_KEY"`
  works directly off `.env` and skips the browser flow entirely.
- **The seeded org's Connect account is a test fixture** —
  `acct_1TzKahA8JbdmiBB4`, linked to `maple-street-market`, charges + payouts
  enabled. Recreate with `scripts/create-test-connect-account.mjs` +
  `scripts/link-test-connect-account.mjs` if the local DB is ever reseeded.
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
