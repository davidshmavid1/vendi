import Link from "next/link";
import { requireOrgContext, requireRole, tenantWhere } from "@/lib/authz";
import { prisma } from "@/lib/prisma";
import { Card, Badge } from "@/components/ui";
import { CreateEventForm } from "@/components/forms/CreateEventForm";

export default async function EventsListPage({
  params,
}: {
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  const { ctx } = await requireOrgContext(orgSlug);
  requireRole(ctx, ["ORGANIZER"]);

  const events = await prisma.event.findMany({
    where: tenantWhere(ctx),
    orderBy: { startDate: "desc" },
    include: { _count: { select: { applications: true, spaces: true } } },
  });

  return (
    <div className="mx-auto grid w-full max-w-4xl flex-1 gap-8 px-6 py-10 md:grid-cols-[1fr_320px]">
      <div>
        <h1 className="text-2xl font-semibold">Events</h1>
        <div className="mt-4 flex flex-col gap-3">
          {events.map((event) => (
            <Link key={event.id} href={`/o/${orgSlug}/dashboard/events/${event.id}`}>
              <Card className="flex items-center justify-between transition-colors hover:border-zinc-400 dark:hover:border-zinc-600">
                <div>
                  <p className="font-medium">{event.name}</p>
                  <p className="text-sm text-zinc-500">
                    {event.startDate.toLocaleDateString()} · {event._count.spaces} spaces ·{" "}
                    {event._count.applications} applications
                  </p>
                </div>
                <Badge>{event.status}</Badge>
              </Card>
            </Link>
          ))}
          {events.length === 0 && <p className="text-sm text-zinc-500">No events yet.</p>}
        </div>
      </div>
      <div>
        <h2 className="mb-3 text-lg font-semibold">New event</h2>
        <CreateEventForm orgSlug={orgSlug} />
      </div>
    </div>
  );
}
