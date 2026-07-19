import Link from "next/link";
import { requireOrgContext, requireRole } from "@/lib/authz";
import { Card } from "@/components/ui";

/**
 * Unlike the Stripe Connect return page, this doesn't synchronously poll
 * Stripe — the webhook (checkout.session.completed) is the source of truth
 * for subscription status, and it fires reliably on its own.
 */
export default async function BillingReturnPage({
  params,
}: {
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  const { ctx } = await requireOrgContext(orgSlug);
  requireRole(ctx, ["ORGANIZER"]);

  return (
    <div className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center px-6 py-16">
      <Card>
        <p className="font-medium">Thanks!</p>
        <p className="mt-1 text-sm text-zinc-500">
          We&apos;re confirming your subscription now — it should show as active in a moment.
        </p>
        <Link
          href={`/o/${orgSlug}/dashboard/settings/billing`}
          className="mt-4 inline-block text-sm underline"
        >
          Back to billing
        </Link>
      </Card>
    </div>
  );
}
