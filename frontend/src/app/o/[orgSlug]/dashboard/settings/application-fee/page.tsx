import { requireOrgContext, requireRole } from "@/lib/authz";
import { Card } from "@/components/ui";
import { ApplicationFeeForm } from "@/components/forms/ApplicationFeeForm";

export default async function ApplicationFeeSettingsPage({
  params,
}: {
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  const { ctx, organization } = await requireOrgContext(orgSlug);
  requireRole(ctx, ["ORGANIZER"]);

  return (
    <div className="mx-auto w-full max-w-2xl flex-1 px-6 py-10">
      <h1 className="text-2xl font-semibold">Application fee</h1>
      <p className="mt-1 text-sm text-zinc-500">
        Applies to every event you create — vendors pay this once, when they submit an
        application.
      </p>
      <Card className="mt-6">
        <ApplicationFeeForm orgSlug={orgSlug} currentFee={organization.vendorApplicationFee} />
      </Card>
    </div>
  );
}
