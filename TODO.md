# TODO

Running checklist for picking this back up across sessions. Check things off
as they get done — this isn't the long-term roadmap (see README.md's
Phase 2/3 section for that), it's "what's actually next."

## New since last pass: subscription billing + application fees

Business model expanded to three money flows — see README.md's top section
for the full breakdown. All built and verified locally:

- [x] Platform cut on stall bookings: 5% → 1% default
- [x] Vendor application fee (org-wide, 0% platform cut) — settings page,
      payment flow, webhook confirmation all verified with a real test payment
- [x] Organizer → Vendi subscription ($20/mo, Stripe Checkout) — verified with
      a real test-mode subscription synced via webhook
- [ ] Nothing left locally on this — next real step is wiring the same env
      vars into Vercel (below) so it works in production too

Note: local Stripe test mode now has a real (test-mode, harmless) Connect
account and subscription created during verification — fine to leave, or
clean up later from the Stripe dashboard if you want a tidier test account.

## Deployment (in progress — goal: live demo link this week)

- [x] Push code to GitHub, connect the repo to Vercel
- [x] Fix the Vercel build (missing `postinstall` script for Prisma Client)
- [x] Fix Vercel's commit-author-email deploy block
- [x] Get real Stripe test keys working locally, incl. webhook secret via
      `stripe listen` (see README's "Local Stripe webhooks" section)
- [ ] Create a cloud Postgres — Neon or Supabase are the easiest free options
- [ ] Apply the schema to it (`prisma migrate deploy` or `prisma db push`,
      pointed at the cloud `DATABASE_URL`)
- [ ] Seed it with demo data if useful (`npm run db:seed`, same `DATABASE_URL`)
- [ ] Add environment variables in Vercel (Settings → Environment Variables):
  - [ ] `DATABASE_URL`
  - [ ] `AUTH_SECRET` (generate with `npx auth secret`)
  - [ ] `NEXTAUTH_URL` and `NEXT_PUBLIC_APP_URL` — the real Vercel URL
  - [ ] `STRIPE_SECRET_KEY` / `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY` — same real
        test-mode keys now working locally
  - [ ] `STRIPE_SUBSCRIPTION_PRICE_ID` — same one from local `.env`
  - [ ] `STRIPE_WEBHOOK_SECRET` — once the webhook below exists (this will be
        a **different** `whsec_...` than the local one — each webhook
        endpoint you register gets its own secret)
- [ ] Set up a real Stripe webhook (Dashboard → Developers → Webhooks, not the
      CLI) pointed at `https://<your-vercel-url>/api/webhooks/stripe`
- [ ] Click through the full flow (signup → event → apply → pay application
      fee → approve → pay for a space → confirm) on the live URL before the
      demo

## After hosting is working

Back to local dev/learning for a while — no rush on Phase 2 until the demo's
done and you've spent more time with the architecture and API.

- Phase 1 is feature-complete and was verified end-to-end locally (see
  README.md and ARCHITECTURE.md).
- Phase 2 (waitlist automation, real email delivery, audit-log dashboards) —
  not started.
- Phase 3 (cross-org vendor profiles, analytics, configurable workflows) —
  not started.
