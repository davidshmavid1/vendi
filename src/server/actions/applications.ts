"use server";

import { revalidatePath } from "next/cache";
import { requireOrgContext } from "@/lib/authz";
import {
  submitApplication,
  approveApplication,
  denyApplication,
  withdrawApplication,
} from "@/domain/applicationStateMachine";

export async function submitApplicationAction(orgSlug: string, eventId: string) {
  const { ctx } = await requireOrgContext(orgSlug);
  await submitApplication(ctx, { eventId });
  revalidatePath(`/o/${orgSlug}/vendor/dashboard`);
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
