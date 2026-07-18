import { NextRequest, NextResponse } from "next/server";
import type Stripe from "stripe";
import { stripe } from "@/lib/stripe";
import { prisma } from "@/lib/prisma";
import { confirmBookingPayment } from "@/domain/bookingStateMachine";

export const runtime = "nodejs";

/**
 * Payment truth comes only from here. Nothing client-reported ever flips a
 * Booking to CONFIRMED — only a Stripe event whose signature we've verified
 * against STRIPE_WEBHOOK_SECRET.
 */
export async function POST(req: NextRequest) {
  const signature = req.headers.get("stripe-signature");
  const webhookSecret = process.env.STRIPE_WEBHOOK_SECRET;
  if (!signature || !webhookSecret) {
    return NextResponse.json({ error: "Missing signature" }, { status: 400 });
  }

  const rawBody = await req.text();

  let event: Stripe.Event;
  try {
    event = stripe.webhooks.constructEvent(rawBody, signature, webhookSecret);
  } catch (err) {
    console.error("Stripe webhook signature verification failed", err);
    return NextResponse.json({ error: "Invalid signature" }, { status: 400 });
  }

  switch (event.type) {
    case "payment_intent.succeeded": {
      const paymentIntent = event.data.object as Stripe.PaymentIntent;
      try {
        await confirmBookingPayment({ stripePaymentIntentId: paymentIntent.id });
      } catch (err) {
        console.error("Failed to confirm booking payment", err);
        // Non-2xx tells Stripe to retry — appropriate for anything other
        // than an already-processed event, which confirmBookingPayment
        // already treats as a no-op rather than throwing.
        return NextResponse.json({ error: "Failed to process payment" }, { status: 500 });
      }
      break;
    }
    case "payment_intent.payment_failed": {
      const paymentIntent = event.data.object as Stripe.PaymentIntent;
      await prisma.payment.updateMany({
        where: { stripePaymentIntentId: paymentIntent.id },
        data: { status: "FAILED" },
      });
      break;
    }
    default:
      break;
  }

  return NextResponse.json({ received: true });
}
