"use server";

import { revalidatePath } from "next/cache";
import { requireOrgContext } from "@/lib/authz";
import {
  createPendingApplication,
  approveApplication,
  denyApplication,
  withdrawApplication,
} from "@/domain/applicationStateMachine";
import { initiateApplicationFeePayment } from "@/domain/applicationFeePayments";

/** Used when the org doesn't charge a vendor application fee — instant, no Stripe involved. */
export async function submitApplicationAction(orgSlug: string, eventId: string) {
  const { ctx } = await requireOrgContext(orgSlug);
  await createPendingApplication(ctx, { eventId });
  revalidatePath(`/o/${orgSlug}/vendor/dashboard`);
}

export type InitiateApplicationFeeResult =
  | { ok: true; clientSecret: string; applicationId: string }
  | { ok: false; error: string };

/** Used when the org does charge a fee — returns a clientSecret for Stripe Elements. */
export async function initiateApplicationFeePaymentAction(
  orgSlug: string,
  eventId: string,
): Promise<InitiateApplicationFeeResult> {
  const { ctx } = await requireOrgContext(orgSlug);

  try {
    const { application, clientSecret } = await initiateApplicationFeePayment(ctx, { eventId });
    return { ok: true, clientSecret, applicationId: application.id };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "Could not start application",
    };
  }
}

export async function approveApplicationAction(orgSlug: string, applicationId: string) {
  const { ctx } = await requireOrgContext(orgSlug);
  await approveApplication(ctx, applicationId);
  revalidatePath(`/o/${orgSlug}/dashboard/applications`);
}

export async function denyApplicationAction(orgSlug: string, applicationId: string) {
  const { ctx } = await requireOrgContext(orgSlug);
  await denyApplication(ctx, applicationId);
  revalidatePath(`/o/${orgSlug}/dashboard/applications`);
}

export async function withdrawApplicationAction(orgSlug: string, applicationId: string) {
  const { ctx } = await requireOrgContext(orgSlug);
  await withdrawApplication(ctx, applicationId);
  revalidatePath(`/o/${orgSlug}/vendor/dashboard`);
}
