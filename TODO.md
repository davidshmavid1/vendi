# TODO

Running checklist for picking this back up across sessions. Check things off
as they get done — this isn't the long-term roadmap (see README.md's
Phase 2/3 section for that), it's "what's actually next."

## Deployment (in progress — goal: live demo link this week)

- [x] Push code to GitHub, connect the repo to Vercel
- [x] Fix the Vercel build (missing `postinstall` script for Prisma Client)
- [ ] Create a cloud Postgres — Neon or Supabase are the easiest free options
- [ ] Apply the schema to it (`prisma migrate deploy` or `prisma db push`,
      pointed at the cloud `DATABASE_URL`)
- [ ] Seed it with demo data if useful (`npm run db:seed`, same `DATABASE_URL`)
- [ ] Add environment variables in Vercel (Settings → Environment Variables):
  - [ ] `DATABASE_URL`
  - [ ] `AUTH_SECRET` (generate with `npx auth secret`)
  - [ ] `NEXTAUTH_URL` and `NEXT_PUBLIC_APP_URL` — the real Vercel URL
  - [ ] `STRIPE_SECRET_KEY` / `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY` — real
        Stripe test-mode keys (still placeholders in local `.env` too)
  - [ ] `STRIPE_WEBHOOK_SECRET` — once the webhook below exists
- [ ] Set up a Stripe webhook pointed at
      `https://<your-vercel-url>/api/webhooks/stripe`
- [ ] Click through the full flow (signup → event → apply → approve → pay →
      confirm) on the live URL before the demo

## After hosting is working

Back to local dev/learning for a while — no rush on Phase 2 until the demo's
done and you've spent more time with the architecture and API.

- Phase 1 is feature-complete and was verified end-to-end locally (see
  README.md and ARCHITECTURE.md).
- Phase 2 (waitlist automation, real email delivery, audit-log dashboards) —
  not started.
- Phase 3 (cross-org vendor profiles, analytics, configurable workflows) —
  not started.
