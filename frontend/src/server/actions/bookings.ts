"use server";

import { requireOrgContext } from "@/lib/authz";
import { initiateBookingPayment } from "@/domain/payments";
import { cancelBooking } from "@/domain/bookingStateMachine";
import { revalidatePath } from "next/cache";

export type InitiateBookingResult =
  | { ok: true; clientSecret: string; bookingId: string }
  | { ok: false; error: string };

/** Called from a Client Component so the returned clientSecret can drive
 * Stripe Elements' payment confirmation in the browser. */
export async function initiateBookingAction(
  orgSlug: string,
  spaceId: string,
): Promise<InitiateBookingResult> {
  const { ctx } = await requireOrgContext(orgSlug);

  try {
    const { booking, clientSecret } = await initiateBookingPayment(ctx, { spaceId });
    return { ok: true, clientSecret, bookingId: booking.id };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "Could not start booking" };
  }
}

export async function cancelBookingAction(orgSlug: string, bookingId: string) {
  const { ctx } = await requireOrgContext(orgSlug);
  await cancelBooking(ctx, bookingId);
  revalidatePath(`/o/${orgSlug}/dashboard`);
  revalidatePath(`/o/${orgSlug}/vendor/dashboard`);
}
