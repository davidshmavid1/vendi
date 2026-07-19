import { stripe } from "@/lib/stripe";
import { prisma } from "@/lib/prisma";
import { createPendingApplication } from "./applicationStateMachine";
import { requireVendor, type AuthContext } from "@/lib/authz";
import { NotFoundError, InvalidTransitionError } from "@/lib/errors";
import type { ApplicationModel } from "@/generated/prisma/models";

/**
 * Composes the application state machine with Stripe, mirroring
 * initiateBookingPayment in payments.ts — except the platform takes 0% of
 * this charge (deliberately, per the business: no cut on application fees,
 * unlike stall bookings). Only called when the org actually charges a fee;
 * the zero-fee path in createPendingApplication never reaches this function.
 */
export async function initiateApplicationFeePayment(
  ctx: AuthContext,
  params: { eventId: string },
): Promise<{ application: ApplicationModel; clientSecret: string }> {
  requireVendor(ctx);

  const organization = await prisma.organization.findFirst({ where: { id: ctx.organizationId } });
  if (!organization) throw new NotFoundError("Organization not found");
  if (!organization.stripeConnectAccountId || !organization.stripeOnboardingComplete) {
    throw new InvalidTransitionError("This organizer hasn't finished connecting Stripe yet");
  }
  if (organization.vendorApplicationFee <= 0) {
    throw new InvalidTransitionError("This event doesn't require an application fee");
  }

  const application = await createPendingApplication(ctx, { eventId: params.eventId });

  const paymentIntent = await stripe.paymentIntents.create({
    amount: organization.vendorApplicationFee,
    currency: "usd",
    application_fee_amount: 0,
    transfer_data: { destination: organization.stripeConnectAccountId },
    metadata: {
      applicationId: application.id,
      organizationId: ctx.organizationId,
      purpose: "APPLICATION_FEE",
    },
  });

  if (!paymentIntent.client_secret) {
    throw new Error("Stripe did not return a client secret");
  }

  await prisma.payment.create({
    data: {
      organizationId: ctx.organizationId,
      purpose: "APPLICATION_FEE",
      applicationId: application.id,
      stripePaymentIntentId: paymentIntent.id,
      amount: organization.vendorApplicationFee,
      applicationFeeAmount: 0,
      currency: "usd",
      status: "PENDING",
    },
  });

  return { application, clientSecret: paymentIntent.client_secret };
}
