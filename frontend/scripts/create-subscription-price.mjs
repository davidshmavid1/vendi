import "dotenv/config";
import Stripe from "stripe";

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY);

const product = await stripe.products.create({
  name: "Vendi organizer subscription",
  description: "Monthly subscription for market organizers using Vendi.",
});

const price = await stripe.prices.create({
  product: product.id,
  unit_amount: 2000, // $20.00
  currency: "usd",
  recurring: { interval: "month" },
});

console.log("Product ID:", product.id);
console.log("Price ID:", price.id);
console.log("\nAdd this to .env:");
console.log(`STRIPE_SUBSCRIPTION_PRICE_ID="${price.id}"`);
