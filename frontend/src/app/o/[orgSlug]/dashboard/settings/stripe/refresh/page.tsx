import { redirect } from "next/navigation";
import { requireOrgContext, requireRole } from "@/lib/authz";
import { createConnectOnboardingLink } from "@/domain/stripeConnect";

// Stripe sends the organizer back here if their onboarding link expired
// before they finished — we just mint a fresh one and continue.
export default async function StripeRefreshPage({
  params,
}: {
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  const { ctx } = await requireOrgContext(orgSlug);
  requireRole(ctx, ["ORGANIZER"]);

  const url = await createConnectOnboardingLink(ctx, orgSlug);
  redirect(url);
}
