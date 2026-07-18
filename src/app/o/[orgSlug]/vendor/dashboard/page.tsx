import Link from "next/link";
import { requireOrgContext, requireRole, requireVendor, vendorRowWhere, tenantWhere } from "@/lib/authz";
import { prisma } from "@/lib/prisma";
import { Card, Badge } from "@/components/ui";
import { withdrawApplicationAction } from "@/server/actions/applications";

export default async function VendorDashboardPage({
  params,
}: {
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  const { ctx } = await requireOrgContext(orgSlug);
  requireRole(ctx, ["VENDOR"]);
  requireVendor(ctx);

  const [applications, bookings, openEvents] = await Promise.all([
    prisma.application.findMany({
      where: vendorRowWhere(ctx),
      orderBy: { submittedAt: "desc" },
      include: { event: true },
    }),
    prisma.booking.findMany({
      where: vendorRowWhere(ctx),
      orderBy: { createdAt: "desc" },
      include: { space: { include: { event: true } } },
    }),
    prisma.event.findMany({
      where: { ...tenantWhere(ctx), status: "APPLICATIONS_OPEN" },
      orderBy: { startDate: "asc" },
    }),
  ]);

  const appliedEventIds = new Set(applications.map((a) => a.eventId));
  const applicableEvents = openEvents.filter((e) => !appliedEventIds.has(e.id));
  const approvedWithoutBooking = applications.filter(
    (a) => a.status === "APPROVED" && !bookings.some((b) => b.applicationId === a.id),
  );

  return (
    <div className="mx-auto w-full max-w-3xl flex-1 px-6 py-10">
      <h1 className="text-2xl font-semibold">My applications</h1>
      <div className="mt-4 flex flex-col gap-2">
        {applications.map((application) => (
          <Card key={application.id} className="flex items-center justify-between">
            <div>
              <p className="font-medium">{application.event.name}</p>
              <p className="text-sm text-zinc-500">
                Submitted {application.submittedAt.toLocaleDateString()}
              </p>
            </div>
            <div className="flex items-center gap-3">
              <Badge
                tone={
                  application.status === "APPROVED"
                    ? "green"
                    : application.status === "DENIED"
                      ? "red"
                      : "yellow"
                }
              >
                {application.status}
              </Badge>
              {application.status === "SUBMITTED" && (
                <form action={withdrawApplicationAction.bind(null, orgSlug, application.id)}>
                  <button className="text-sm text-zinc-500 hover:underline">Withdraw</button>
                </form>
              )}
            </div>
          </Card>
        ))}
        {applications.length === 0 && <p className="text-sm text-zinc-500">No applications yet.</p>}
      </div>

      {approvedWithoutBooking.length > 0 && (
        <>
          <h2 className="mt-8 text-lg font-semibold">Ready to book</h2>
          <div className="mt-3 flex flex-col gap-2">
            {approvedWithoutBooking.map((application) => (
              <Card key={application.id} className="flex items-center justify-between">
                <span>{application.event.name}</span>
                <Link
                  href={`/o/${orgSlug}/vendor/events/${application.eventId}`}
                  className="text-sm hover:underline"
                >
                  View spaces →
                </Link>
              </Card>
            ))}
          </div>
        </>
      )}

      <h2 className="mt-8 text-lg font-semibold">My bookings</h2>
      <div className="mt-3 flex flex-col gap-2">
        {bookings.map((booking) => (
          <Card key={booking.id} className="flex items-center justify-between">
            <div>
              <p className="font-medium">
                {booking.space.name} — {booking.space.event.name}
              </p>
              <p className="text-sm text-zinc-500">${(booking.space.price / 100).toFixed(2)}</p>
            </div>
            <Badge
              tone={
                booking.status === "CONFIRMED"
                  ? "green"
                  : booking.status === "PENDING_PAYMENT"
                    ? "yellow"
                    : "red"
              }
            >
              {booking.status}
            </Badge>
          </Card>
        ))}
        {bookings.length === 0 && <p className="text-sm text-zinc-500">No bookings yet.</p>}
      </div>

      <h2 className="mt-8 text-lg font-semibold">Open events</h2>
      <div className="mt-3 flex flex-col gap-2">
        {applicableEvents.map((event) => (
          <Card key={event.id} className="flex items-center justify-between">
            <span>{event.name}</span>
            <Link
              href={`/o/${orgSlug}/vendor/events/${event.id}`}
              className="rounded-md bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white dark:bg-white dark:text-zinc-900"
            >
              Apply
            </Link>
          </Card>
        ))}
        {applicableEvents.length === 0 && (
          <p className="text-sm text-zinc-500">No new events to apply to right now.</p>
        )}
      </div>
    </div>
  );
}
