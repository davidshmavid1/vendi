import { stripe } from "@/lib/stripe";
import { prisma } from "@/lib/prisma";
import { createBooking } from "./bookingStateMachine";
import { requireVendor, type AuthContext } from "@/lib/authz";
import { NotFoundError, InvalidTransitionError } from "@/lib/errors";
import type { BookingModel } from "@/generated/prisma/models";

/**
 * Composes the booking state machine with Stripe: holds the slot
 * (PENDING_PAYMENT), then creates a destination-charge PaymentIntent on the
 * platform account that transfers straight through to the organizer's
 * connected account minus the platform's application fee. The booking is
 * NOT confirmed here — only the signature-verified webhook
 * (confirmBookingPayment) does that.
 */
export async function initiateBookingPayment(
  ctx: AuthContext,
  params: { spaceId: string },
): Promise<{ booking: BookingModel; clientSecret: string }> {
  requireVendor(ctx);

  const organization = await prisma.organization.findFirst({ where: { id: ctx.organizationId } });
  if (!organization) throw new NotFoundError("Organization not found");
  if (!organization.stripeConnectAccountId || !organization.stripeOnboardingComplete) {
    throw new InvalidTransitionError("This organizer hasn't finished connecting Stripe yet");
  }

  const space = await prisma.space.findFirst({
    where: { id: params.spaceId, organizationId: ctx.organizationId },
  });
  if (!space) throw new NotFoundError("Space not found");

  // Re-validates availability + approved-application guards inside its own
  // transaction; we don't duplicate that logic here.
  const booking = await createBooking(ctx, { spaceId: params.spaceId });

  const applicationFeeAmount = Math.round((space.price * organization.applicationFeeBps) / 10_000);

  const paymentIntent = await stripe.paymentIntents.create({
    amount: space.price,
    currency: "usd",
    automatic_payment_methods: { enabled: true },
    application_fee_amount: applicationFeeAmount,
    transfer_data: { destination: organization.stripeConnectAccountId },
    metadata: { bookingId: booking.id, organizationId: ctx.organizationId, purpose: "BOOKING_FEE" },
  });

  if (!paymentIntent.client_secret) {
    throw new Error("Stripe did not return a client secret");
  }

  await prisma.payment.create({
    data: {
      organizationId: ctx.organizationId,
      purpose: "BOOKING_FEE",
      bookingId: booking.id,
      stripePaymentIntentId: paymentIntent.id,
      amount: space.price,
      applicationFeeAmount,
      currency: "usd",
      status: "PENDING",
    },
  });

  return { booking, clientSecret: paymentIntent.client_secret };
}
