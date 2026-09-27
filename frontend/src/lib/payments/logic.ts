import type { PaymentStateName } from "./types";

/** States whose outcome is still being decided by the server. */
export function isSettling(state: PaymentStateName, returnedFromCheckout: boolean): boolean {
  // After a redirect back from Stripe the webhook may lag a few seconds, so
  // an open checkout is worth re-reading too.
  return state === "PROCESSING" || state === "REFUND_PENDING" || (returnedFromCheckout && state === "CHECKOUT_OPEN");
}

export const MAX_POLLS = 20;

/** Bounded polling: quick at first, then slower; null means stop (about two
 *  minutes in total) and offer a manual "Check again". */
export function pollDelayMs(pollsDone: number): number | null {
  if (pollsDone >= MAX_POLLS) return null;
  return pollsDone < 5 ? 2_000 : 8_000;
}

/** Only follow checkout links to Stripe's hosted pages. */
export function isCheckoutUrl(url: string | null | undefined): url is string {
  if (!url) return false;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" && parsed.hostname === "checkout.stripe.com";
  } catch {
    return false;
  }
}
