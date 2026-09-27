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

  // Stripe Tax computes sales tax at checkout. It only charges tax in
  // jurisdictions we hold an active tax registration for — with none
  // registered, this calculates $0 and the customer pays the base price.
  // Turning collection on later is a Stripe-side registration, not a code
  // change. The price is tax-exclusive, so tax is added on top of $20
  // rather than carved out of it.
  const session = await stripe.checkout.sessions.create({
    mode: "subscription",
    customer: stripeCustomerId,
    line_items: [{ price: priceId, quantity: 1 }],
    automatic_tax: { enabled: true },
    // Both are required for automatic_tax on a pre-created Customer: Stripe
    // can't compute tax without a location, and the collected address has to
    // persist back onto the Customer so renewal invoices stay correct.
    billing_address_collection: "required",
    customer_update: { address: "auto" },
    success_url: `${baseUrl}/o/${orgSlug}/dashboard/settings/billing/return`,
    cancel_url: `${baseUrl}/o/${orgSlug}/dashboard/settings/billing`,
    metadata: { organizationId: organization.id },
  });

  if (!session.url) throw new Error("Stripe did not return a Checkout Session URL");

  return session.url;
}
