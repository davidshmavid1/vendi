import { requireOrgContext, requireRole } from "@/lib/authz";
import { Card, Badge } from "@/components/ui";
import { startSubscriptionCheckout } from "@/server/actions/subscriptions";

export default async function BillingSettingsPage({
  params,
}: {
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  const { ctx, organization } = await requireOrgContext(orgSlug);
  requireRole(ctx, ["ORGANIZER"]);

  const isActive = organization.subscriptionStatus === "ACTIVE";

  return (
    <div className="mx-auto w-full max-w-2xl flex-1 px-6 py-10">
      <h1 className="text-2xl font-semibold">Billing</h1>
      <Card className="mt-6 flex items-center justify-between">
        <div>
          <p className="font-medium">Vendi subscription — $20/month</p>
          {organization.subscriptionCurrentPeriodEnd && (
            <p className="text-sm text-zinc-500">
              {isActive ? "Renews" : "Ended"}{" "}
              {organization.subscriptionCurrentPeriodEnd.toLocaleDateString()}
            </p>
          )}
        </div>
        <Badge tone={isActive ? "green" : "yellow"}>{organization.subscriptionStatus}</Badge>
      </Card>

      {!isActive && (
        <form action={startSubscriptionCheckout.bind(null, orgSlug)} className="mt-6">
          <button
            type="submit"
            className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white dark:bg-white dark:text-zinc-900"
          >
            Subscribe
          </button>
        </form>
      )}
    </div>
  );
}
