import { notFound } from "next/navigation";
import { requireOrgContext, requireRole, tenantWhere } from "@/lib/authz";
import { prisma } from "@/lib/prisma";
import { Card, Badge } from "@/components/ui";
import { CreateSpaceForm } from "@/components/forms/CreateSpaceForm";
import { setEventApplicationsOpen } from "@/server/actions/events";
import { approveApplicationAction, denyApplicationAction } from "@/server/actions/applications";

export default async function EventDetailPage({
  params,
}: {
  params: Promise<{ orgSlug: string; eventId: string }>;
}) {
  const { orgSlug, eventId } = await params;
  const { ctx } = await requireOrgContext(orgSlug);
  requireRole(ctx, ["ORGANIZER"]);

  const event = await prisma.event.findFirst({
    where: { id: eventId, ...tenantWhere(ctx) },
    include: {
      spaces: { orderBy: { createdAt: "asc" } },
      applications: {
        orderBy: { submittedAt: "desc" },
        include: { vendor: true },
      },
    },
  });
  if (!event) notFound();

  const applicationsOpen = event.status === "APPLICATIONS_OPEN";

  return (
    <div className="mx-auto w-full max-w-4xl flex-1 px-6 py-10">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold">{event.name}</h1>
          <p className="text-sm text-zinc-500">
            {event.startDate.toLocaleDateString()} – {event.endDate.toLocaleDateString()}
            {event.location ? ` · ${event.location}` : ""}
          </p>
        </div>
        <form
          action={setEventApplicationsOpen.bind(null, orgSlug, event.id, !applicationsOpen)}
        >
          <button
            type="submit"
            className="rounded-md border border-zinc-300 px-4 py-2 text-sm font-medium dark:border-zinc-700"
          >
            {applicationsOpen ? "Close applications" : "Open applications"}
          </button>
        </form>
      </div>
      <div className="mt-2">
        <Badge tone={applicationsOpen ? "green" : "zinc"}>{event.status}</Badge>
      </div>

      <div className="mt-10 grid gap-8 md:grid-cols-[1fr_320px]">
        <div>
          <h2 className="text-lg font-semibold">Spaces</h2>
          <div className="mt-3 flex flex-col gap-2">
            {event.spaces.map((space) => (
              <Card key={space.id} className="flex items-center justify-between">
                <div>
                  <p className="font-medium">{space.name}</p>
                  <p className="text-sm text-zinc-500">
                    ${(space.price / 100).toFixed(2)} · {space.availableQty}/{space.totalQty} available
                  </p>
                </div>
                <Badge tone={space.status === "AVAILABLE" ? "green" : "red"}>{space.status}</Badge>
              </Card>
            ))}
            {event.spaces.length === 0 && (
              <p className="text-sm text-zinc-500">No spaces defined yet.</p>
            )}
          </div>
        </div>
        <div>
          <h2 className="mb-3 text-lg font-semibold">New space</h2>
          <CreateSpaceForm orgSlug={orgSlug} eventId={event.id} />
        </div>
      </div>

      <div className="mt-10">
        <h2 className="text-lg font-semibold">Applications</h2>
        <div className="mt-3 flex flex-col gap-2">
          {event.applications.map((application) => (
            <Card key={application.id} className="flex items-center justify-between">
              <div>
                <p className="font-medium">{application.vendor.businessName}</p>
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
                  <div className="flex gap-2">
                    <form action={approveApplicationAction.bind(null, orgSlug, application.id)}>
                      <button className="rounded-md bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white dark:bg-white dark:text-zinc-900">
                        Approve
                      </button>
                    </form>
                    <form action={denyApplicationAction.bind(null, orgSlug, application.id)}>
                      <button className="rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium dark:border-zinc-700">
                        Deny
                      </button>
                    </form>
                  </div>
                )}
              </div>
            </Card>
          ))}
          {event.applications.length === 0 && (
            <p className="text-sm text-zinc-500">No applications yet.</p>
          )}
        </div>
      </div>
    </div>
  );
}
