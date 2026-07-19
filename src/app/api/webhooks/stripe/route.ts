import { NextRequest, NextResponse } from "next/server";
import type Stripe from "stripe";
import { stripe } from "@/lib/stripe";
import { prisma } from "@/lib/prisma";
import { confirmBookingPayment } from "@/domain/bookingStateMachine";
import { confirmApplicationFeePayment } from "@/domain/applicationStateMachine";
import { writeAuditEvent } from "@/domain/audit";
import type { SubscriptionStatus } from "@/generated/prisma/enums";

export const runtime = "nodejs";

/** Vendi doesn't layer its own rules on subscription status, so this is a
 * simplified, lossy mirror of Stripe's richer set (trialing/incomplete/etc.
 * collapse into the closest of our four). */
function toSubscriptionStatus(stripeStatus: Stripe.Subscription.Status): SubscriptionStatus {
  switch (stripeStatus) {
    case "active":
    case "trialing":
      return "ACTIVE";
    case "past_due":
    case "unpaid":
    case "incomplete":
      return "PAST_DUE";
    case "canceled":
    case "incomplete_expired":
    case "paused":
      return "CANCELED";
    default:
      return "PAST_DUE";
  }
}

async function syncSubscriptionToOrganization(subscription: Stripe.Subscription) {
  const customerId =
    typeof subscription.customer === "string" ? subscription.customer : subscription.customer.id;

  const organization = await prisma.organization.findFirst({
    where: { stripeCustomerId: customerId },
  });
  if (!organization) {
    console.error("No organization found for Stripe customer", customerId);
    return;
  }

  const periodEndSeconds = subscription.items.data[0]?.current_period_end;
  const status = toSubscriptionStatus(subscription.status);

  await prisma.$transaction(async (tx) => {
    await tx.organization.update({
      where: { id: organization.id },
      data: {
        stripeSubscriptionId: subscription.id,
        subscriptionStatus: status,
        subscriptionCurrentPeriodEnd: periodEndSeconds ? new Date(periodEndSeconds * 1000) : null,
      },
    });
    await writeAuditEvent(tx, {
      organizationId: organization.id,
      entityType: "Organization",
      entityId: organization.id,
      action: "SUBSCRIPTION_STATUS_CHANGED",
      fromState: organization.subscriptionStatus,
      toState: status,
      actorId: null,
      payload: { stripeSubscriptionId: subscription.id },
    });
  });
}

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
        // The Payment row (not Stripe metadata) is the source of truth for
        // which kind of payment this is — look it up first, then route to
        // the matching domain confirm function.
        const payment = await prisma.payment.findUnique({
          where: { stripePaymentIntentId: paymentIntent.id },
        });
        if (!payment) {
          console.error("No Payment row found for succeeded PaymentIntent", paymentIntent.id);
          break;
        }

        if (payment.purpose === "APPLICATION_FEE") {
          await confirmApplicationFeePayment({ stripePaymentIntentId: paymentIntent.id });
        } else {
          await confirmBookingPayment({ stripePaymentIntentId: paymentIntent.id });
        }
      } catch (err) {
        console.error("Failed to confirm payment", err);
        // Non-2xx tells Stripe to retry — appropriate for anything other
        // than an already-processed event, which both confirm functions
        // already treat as a no-op rather than throwing.
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
    case "checkout.session.completed": {
      const session = event.data.object as Stripe.Checkout.Session;
      if (session.mode !== "subscription" || !session.subscription) break;
      const subscriptionId =
        typeof session.subscription === "string" ? session.subscription : session.subscription.id;
      const subscription = await stripe.subscriptions.retrieve(subscriptionId);
      await syncSubscriptionToOrganization(subscription);
      break;
    }
    case "customer.subscription.updated":
    case "customer.subscription.deleted": {
      const subscription = event.data.object as Stripe.Subscription;
      await syncSubscriptionToOrganization(subscription);
      break;
    }
    default:
      break;
  }

  return NextResponse.json({ received: true });
}
