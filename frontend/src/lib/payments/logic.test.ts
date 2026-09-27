import { test } from "node:test";
import assert from "node:assert/strict";
import { isCheckoutUrl, isSettling, MAX_POLLS, pollDelayMs } from "./logic.ts";

test("polling is bounded", () => {
  assert.equal(pollDelayMs(0), 2_000);
  assert.equal(pollDelayMs(5), 8_000);
  assert.equal(pollDelayMs(MAX_POLLS), null);
  let total = 0;
  for (let i = 0; pollDelayMs(i) !== null; i++) total += pollDelayMs(i) as number;
  assert.ok(total <= 150_000);
});

test("an open checkout is only re-read after returning from Stripe", () => {
  assert.equal(isSettling("CHECKOUT_OPEN", false), false);
  assert.equal(isSettling("CHECKOUT_OPEN", true), true);
  assert.equal(isSettling("PROCESSING", false), true);
  assert.equal(isSettling("BOOKED", true), false);
});

test("only Stripe-hosted checkout links are followed", () => {
  assert.equal(isCheckoutUrl("https://checkout.stripe.com/c/pay/cs_test_1"), true);
  assert.equal(isCheckoutUrl("http://checkout.stripe.com/c/pay"), false);
  assert.equal(isCheckoutUrl("https://evil.example/checkout.stripe.com"), false);
  assert.equal(isCheckoutUrl(null), false);
});
