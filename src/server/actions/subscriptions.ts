"use server";

import { redirect } from "next/navigation";
import { requireOrgContext } from "@/lib/authz";
import { createSubscriptionCheckoutSession } from "@/domain/subscriptions";

export async function startSubscriptionCheckout(orgSlug: string) {
  const { ctx } = await requireOrgContext(orgSlug);
  const url = await createSubscriptionCheckoutSession(ctx, orgSlug);
  redirect(url);
}
