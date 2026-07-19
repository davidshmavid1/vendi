import { stripe } from "@/lib/stripe";
import { prisma } from "@/lib/prisma";
import { requireRole, type AuthContext } from "@/lib/authz";
import { NotFoundError } from "@/lib/errors";

/**
 * Organizer's own subscription to Vendi — a genuinely different Stripe
 * product (Billing/Checkout, not Connect) from everything else in this
 * codebase. There's no state machine of ours layered on top: Stripe owns
 * the subscription lifecycle, and the webhook just mirrors its status onto
 * the Organization row (see the webhook route's checkout.session.completed /
 * customer.subscription.* handling).
 */
export async function createSubscriptionCheckoutSession(
  ctx: AuthContext,
  orgSlug: string,
): Promise<string> {
  requireRole(ctx, ["ORGANIZER"]);

  const priceId = process.env.STRIPE_SUBSCRIPTION_PRICE_ID;
  if (!priceId) throw new Error("STRIPE_SUBSCRIPTION_PRICE_ID is not set");

  const organization = await prisma.organization.findFirst({ where: { id: ctx.organizationId } });
  if (!organization) throw new NotFoundError("Organization not found");

  let stripeCustomerId = organization.stripeCustomerId;
  if (!stripeCustomerId) {
    const customer = await stripe.customers.create({
      name: organization.name,
      metadata: { organizationId: organization.id },
    });
    stripeCustomerId = customer.id;
    await prisma.organization.update({
      where: { id: organization.id },
      data: { stripeCustomerId },
    });
  }

  const baseUrl = process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000";

  const session = await stripe.checkout.sessions.create({
    mode: "subscription",
    customer: stripeCustomerId,
    line_items: [{ price: priceId, quantity: 1 }],
    success_url: `${baseUrl}/o/${orgSlug}/dashboard/settings/billing/return`,
    cancel_url: `${baseUrl}/o/${orgSlug}/dashboard/settings/billing`,
    metadata: { organizationId: organization.id },
  });

  if (!session.url) throw new Error("Stripe did not return a Checkout Session URL");

  return session.url;
}
