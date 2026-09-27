import { requireOrgContext, requireRole } from "@/lib/authz";
import { Card, Badge } from "@/components/ui";
import { startStripeOnboarding } from "@/server/actions/stripeConnect";

export default async function StripeSettingsPage({
  params,
}: {
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  const { ctx, organization } = await requireOrgContext(orgSlug);
  requireRole(ctx, ["ORGANIZER"]);

  return (
    <div className="mx-auto w-full max-w-2xl flex-1 px-6 py-10">
      <h1 className="text-2xl font-semibold">Stripe</h1>
      <Card className="mt-6 flex items-center justify-between">
        <div>
          <p className="font-medium">
            {organization.stripeOnboardingComplete ? "Connected" : "Not connected"}
          </p>
          <p className="text-sm text-zinc-500">
            Platform fee: {(organization.applicationFeeBps / 100).toFixed(2)}% per booking
          </p>
        </div>
        <Badge tone={organization.stripeOnboardingComplete ? "green" : "yellow"}>
          {organization.stripeOnboardingComplete ? "Ready for payments" : "Setup required"}
        </Badge>
      </Card>

      {!organization.stripeOnboardingComplete && (
        <form action={startStripeOnboarding.bind(null, orgSlug)} className="mt-6">
          <button
            type="submit"
            className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white dark:bg-white dark:text-zinc-900"
          >
            Connect Stripe
          </button>
        </form>
      )}
    </div>
  );
}
