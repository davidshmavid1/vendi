import { notFound } from "next/navigation";
import { requireAdminContext } from "@/lib/authz";
import { prisma } from "@/lib/prisma";
import { Card, Badge } from "@/components/ui";

export default async function AdminOrgDetailPage({
  params,
}: {
  params: Promise<{ organizationId: string }>;
}) {
  await requireAdminContext();
  const { organizationId } = await params;

  const organization = await prisma.organization.findUnique({
    where: { id: organizationId },
    include: {
      events: { orderBy: { startDate: "desc" } },
      _count: { select: { vendors: true, bookings: true, applications: true } },
    },
  });
  if (!organization) notFound();

  const [recentAuditEvents, revenue] = await Promise.all([
    prisma.auditEvent.findMany({
      where: { organizationId },
      orderBy: { createdAt: "desc" },
      take: 20,
    }),
    prisma.payment.aggregate({
      where: { organizationId, status: "SUCCEEDED" },
      _sum: { amount: true, applicationFeeAmount: true },
    }),
  ]);

  return (
    <div className="mx-auto w-full max-w-4xl flex-1 px-6 py-12">
      <h1 className="text-2xl font-semibold">{organization.name}</h1>
      <p className="text-sm text-zinc-500">
        /{organization.slug} · plan {organization.plan}
      </p>

      <div className="mt-6 grid grid-cols-3 gap-4">
        <Card>
          <p className="text-sm text-zinc-500">Vendors</p>
          <p className="text-2xl font-semibold">{organization._count.vendors}</p>
        </Card>
        <Card>
          <p className="text-sm text-zinc-500">Bookings</p>
          <p className="text-2xl font-semibold">{organization._count.bookings}</p>
        </Card>
        <Card>
          <p className="text-sm text-zinc-500">Gross revenue</p>
          <p className="text-2xl font-semibold">
            ${((revenue._sum.amount ?? 0) / 100).toFixed(2)}
          </p>
        </Card>
      </div>

      <h2 className="mt-8 text-lg font-semibold">Events</h2>
      <div className="mt-3 flex flex-col gap-2">
        {organization.events.map((event) => (
          <Card key={event.id} className="flex items-center justify-between">
            <span>{event.name}</span>
            <Badge>{event.status}</Badge>
          </Card>
        ))}
        {organization.events.length === 0 && (
          <p className="text-sm text-zinc-500">No events yet.</p>
        )}
      </div>

      <h2 className="mt-8 text-lg font-semibold">Recent activity</h2>
      <div className="mt-3 flex flex-col gap-1 text-sm">
        {recentAuditEvents.map((e) => (
          <div
            key={e.id}
            className="flex justify-between border-b border-zinc-100 py-1 dark:border-zinc-800"
          >
            <span>
              {e.action} — {e.entityType}
            </span>
            <span className="text-zinc-500">{e.createdAt.toLocaleString()}</span>
          </div>
        ))}
        {recentAuditEvents.length === 0 && (
          <p className="text-sm text-zinc-500">No activity recorded yet.</p>
        )}
      </div>
    </div>
  );
}
