import "dotenv/config";
import Stripe from "stripe";

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY);

// Stripe's documented test-mode values that auto-verify instantly, so this
// account is immediately payment/payout-capable without the hosted
// onboarding UI. See https://docs.stripe.com/connect/testing. Test-mode
// only — never usable for real money movement.
const account = await stripe.accounts.create({
  type: "custom",
  country: "US",
  email: "test-organizer@maplestreet.test",
  capabilities: {
    card_payments: { requested: true },
    transfers: { requested: true },
  },
  business_type: "individual",
  individual: {
    first_name: "Test",
    last_name: "Organizer",
    email: "test-organizer@maplestreet.test",
    phone: "0000000000",
    dob: { day: 1, month: 1, year: 1990 },
    id_number: "000000000", // full test SSN — triggers instant verification, unlike ssn_last_4
    address: {
      line1: "address_full_match",
      city: "Seattle",
      state: "WA",
      postal_code: "98101",
      country: "US",
    },
  },
  business_profile: {
    product_description: "Popup market space rentals for vendors",
    mcc: "5399", // misc general merchandise
  },
  tos_acceptance: {
    date: Math.floor(Date.now() / 1000),
    ip: "127.0.0.1",
  },
  external_account: {
    object: "bank_account",
    country: "US",
    currency: "usd",
    routing_number: "110000000",
    account_number: "000123456789",
  },
});

console.log("Created test Connect account:", account.id);
console.log("charges_enabled:", account.charges_enabled);
console.log("details_submitted:", account.details_submitted);
