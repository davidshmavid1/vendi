import Link from "next/link";
import { requireOrgContext, requireRole, tenantWhere } from "@/lib/authz";
import { prisma } from "@/lib/prisma";
import { Card, Badge } from "@/components/ui";

export default async function OrganizerOverviewPage({
  params,
}: {
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  const { ctx, organization } = await requireOrgContext(orgSlug);
  requireRole(ctx, ["ORGANIZER"]);

  const [eventCount, pendingApplications, confirmedBookings, revenue] = await Promise.all([
    prisma.event.count({ where: tenantWhere(ctx) }),
    prisma.application.count({ where: { ...tenantWhere(ctx), status: "SUBMITTED" } }),
    prisma.booking.count({ where: { ...tenantWhere(ctx), status: "CONFIRMED" } }),
    prisma.payment.aggregate({
      where: { ...tenantWhere(ctx), status: "SUCCEEDED" },
      _sum: { amount: true },
    }),
  ]);

  return (
    <div className="mx-auto w-full max-w-4xl flex-1 px-6 py-10">
      <h1 className="text-2xl font-semibold">Overview</h1>

      <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Card>
          <p className="text-sm text-zinc-500">Events</p>
          <p className="text-2xl font-semibold">{eventCount}</p>
        </Card>
        <Card>
          <p className="text-sm text-zinc-500">Pending applications</p>
          <p className="text-2xl font-semibold">{pendingApplications}</p>
        </Card>
        <Card>
          <p className="text-sm text-zinc-500">Confirmed bookings</p>
          <p className="text-2xl font-semibold">{confirmedBookings}</p>
        </Card>
        <Card>
          <p className="text-sm text-zinc-500">Revenue</p>
          <p className="text-2xl font-semibold">${((revenue._sum.amount ?? 0) / 100).toFixed(2)}</p>
        </Card>
      </div>

      {!organization.stripeOnboardingComplete && (
        <Card className="mt-6 flex items-center justify-between">
          <div>
            <p className="font-medium">Connect Stripe to start accepting payments</p>
            <p className="text-sm text-zinc-500">Vendors can&apos;t pay for spaces until this is done.</p>
          </div>
          <Link
            href={`/o/${orgSlug}/dashboard/settings/stripe`}
            className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white dark:bg-white dark:text-zinc-900"
          >
            Connect Stripe
          </Link>
        </Card>
      )}

      <div className="mt-8 flex items-center justify-between">
        <h2 className="text-lg font-semibold">Events</h2>
        <Link href={`/o/${orgSlug}/dashboard/events`} className="text-sm hover:underline">
          Manage events →
        </Link>
      </div>
    </div>
  );
}
