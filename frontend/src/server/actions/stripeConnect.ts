"use server";

import { redirect } from "next/navigation";
import { requireOrgContext } from "@/lib/authz";
import { createConnectOnboardingLink, syncConnectAccountStatus } from "@/domain/stripeConnect";

export async function startStripeOnboarding(orgSlug: string) {
  const { ctx } = await requireOrgContext(orgSlug);
  const url = await createConnectOnboardingLink(ctx, orgSlug);
  redirect(url);
}

export async function refreshStripeStatus(orgSlug: string) {
  const { ctx } = await requireOrgContext(orgSlug);
  await syncConnectAccountStatus(ctx);
}
