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

/** Plain-language refund status. Never promises a bank settlement date. */
export function refundStatusText(status: string): string {
  switch (status) {
    case "SUCCEEDED":
      return "Refunded. Your bank may take several days to show it.";
    case "REQUESTED":
    case "PENDING":
      return "Refund in progress.";
    case "REVIEW":
      return "Refund under review by Vendi support.";
    default:
      return "The refund couldn't be completed automatically; Vendi support is handling it.";
  }
}
