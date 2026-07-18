import { stripe } from "@/lib/stripe";
import { prisma } from "@/lib/prisma";
import { requireRole, type AuthContext } from "@/lib/authz";
import { NotFoundError } from "@/lib/errors";

/** Organizer self-serve Stripe Connect onboarding — no platform admin involved. */
export async function ensureConnectAccount(ctx: AuthContext): Promise<string> {
  requireRole(ctx, ["ORGANIZER"]);

  const organization = await prisma.organization.findFirst({ where: { id: ctx.organizationId } });
  if (!organization) throw new NotFoundError("Organization not found");

  if (organization.stripeConnectAccountId) {
    return organization.stripeConnectAccountId;
  }

  const account = await stripe.accounts.create({
    type: "express",
    metadata: { organizationId: organization.id },
  });

  await prisma.organization.update({
    where: { id: organization.id },
    data: { stripeConnectAccountId: account.id },
  });

  return account.id;
}

export async function createConnectOnboardingLink(
  ctx: AuthContext,
  orgSlug: string,
): Promise<string> {
  const accountId = await ensureConnectAccount(ctx);
  const baseUrl = process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000";

  const accountLink = await stripe.accountLinks.create({
    account: accountId,
    refresh_url: `${baseUrl}/o/${orgSlug}/dashboard/settings/stripe/refresh`,
    return_url: `${baseUrl}/o/${orgSlug}/dashboard/settings/stripe/return`,
    type: "account_onboarding",
  });

  return accountLink.url;
}

/** Called when the organizer lands back from Stripe onboarding. */
export async function syncConnectAccountStatus(ctx: AuthContext): Promise<boolean> {
  requireRole(ctx, ["ORGANIZER"]);
  const organization = await prisma.organization.findFirst({ where: { id: ctx.organizationId } });
  if (!organization?.stripeConnectAccountId) return false;

  const account = await stripe.accounts.retrieve(organization.stripeConnectAccountId);
  const complete = Boolean(account.details_submitted && account.charges_enabled);

  await prisma.organization.update({
    where: { id: organization.id },
    data: { stripeOnboardingComplete: complete },
  });

  return complete;
}
