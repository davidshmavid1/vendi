import { redirect } from "next/navigation";
import { requireOrgContext, requireRole } from "@/lib/authz";
import { syncConnectAccountStatus } from "@/domain/stripeConnect";

export default async function StripeReturnPage({
  params,
}: {
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  const { ctx } = await requireOrgContext(orgSlug);
  requireRole(ctx, ["ORGANIZER"]);

  await syncConnectAccountStatus(ctx);
  redirect(`/o/${orgSlug}/dashboard/settings/stripe`);
}
