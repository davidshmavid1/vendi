import "dotenv/config";
import Stripe from "stripe";

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY);
const webhookSecret = process.env.STRIPE_WEBHOOK_SECRET;

const [, , paymentIntentId] = process.argv;
if (!paymentIntentId) {
  console.error("Usage: node scripts/replay-webhook-event.mjs <payment_intent_id>");
  process.exit(1);
}

// Fetch the real, current PaymentIntent and wrap it in a realistic event
// envelope, then sign it with our own webhook secret and POST it to our own
// local endpoint — this exercises the exact same webhook code path a real
// Stripe-delivered event would, for a payment that already genuinely
// succeeded but whose original webhook was never delivered (e.g. because
// `stripe listen` wasn't running at the time).
const paymentIntent = await stripe.paymentIntents.retrieve(paymentIntentId);

const event = {
  id: `evt_replay_${Date.now()}`,
  object: "event",
  type: "payment_intent.succeeded",
  data: { object: paymentIntent },
  api_version: "2026-06-24.dahlia",
  created: Math.floor(Date.now() / 1000),
  livemode: false,
  pending_webhooks: 0,
  request: { id: null, idempotency_key: null },
};

const payload = JSON.stringify(event);
const header = stripe.webhooks.generateTestHeaderString({
  payload,
  secret: webhookSecret,
});

const response = await fetch("http://localhost:3000/api/webhooks/stripe", {
  method: "POST",
  headers: { "Content-Type": "application/json", "stripe-signature": header },
  body: payload,
});

console.log("Response status:", response.status);
console.log(await response.text());
