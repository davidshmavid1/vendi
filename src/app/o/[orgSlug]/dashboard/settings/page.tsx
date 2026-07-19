import Link from "next/link";
import { requireOrgContext, requireRole } from "@/lib/authz";
import { Card, Badge } from "@/components/ui";

export default async function SettingsIndexPage({
  params,
}: {
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  const { ctx, organization } = await requireOrgContext(orgSlug);
  requireRole(ctx, ["ORGANIZER"]);

  return (
    <div className="mx-auto w-full max-w-2xl flex-1 px-6 py-10">
      <h1 className="text-2xl font-semibold">Settings</h1>

      <div className="mt-6 flex flex-col gap-3">
        <Link href={`/o/${orgSlug}/dashboard/settings/stripe`}>
          <Card className="flex items-center justify-between transition-colors hover:border-zinc-400 dark:hover:border-zinc-600">
            <div>
              <p className="font-medium">Stripe</p>
              <p className="text-sm text-zinc-500">Connect the account that receives vendor payments</p>
            </div>
            <Badge tone={organization.stripeOnboardingComplete ? "green" : "yellow"}>
              {organization.stripeOnboardingComplete ? "Connected" : "Setup required"}
            </Badge>
          </Card>
        </Link>

        <Link href={`/o/${orgSlug}/dashboard/settings/billing`}>
          <Card className="flex items-center justify-between transition-colors hover:border-zinc-400 dark:hover:border-zinc-600">
            <div>
              <p className="font-medium">Billing</p>
              <p className="text-sm text-zinc-500">Your Vendi subscription</p>
            </div>
            <Badge tone={organization.subscriptionStatus === "ACTIVE" ? "green" : "yellow"}>
              {organization.subscriptionStatus}
            </Badge>
          </Card>
        </Link>

        <Link href={`/o/${orgSlug}/dashboard/settings/application-fee`}>
          <Card className="flex items-center justify-between transition-colors hover:border-zinc-400 dark:hover:border-zinc-600">
            <div>
              <p className="font-medium">Application fee</p>
              <p className="text-sm text-zinc-500">What vendors pay to apply to your events</p>
            </div>
            <Badge tone={organization.vendorApplicationFee > 0 ? "blue" : "zinc"}>
              ${(organization.vendorApplicationFee / 100).toFixed(2)}
            </Badge>
          </Card>
        </Link>
      </div>
    </div>
  );
}
