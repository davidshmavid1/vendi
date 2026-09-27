import Link from "next/link";
import { notFound } from "next/navigation";
import { requireOrgContext, requireRole, requireVendor, tenantWhere } from "@/lib/authz";
import { prisma } from "@/lib/prisma";
import { Card, Badge } from "@/components/ui";
import { submitApplicationAction } from "@/server/actions/applications";
import { ApplicationFeeCheckout } from "@/components/forms/ApplicationFeeCheckout";

export default async function VendorEventPage({
  params,
}: {
  params: Promise<{ orgSlug: string; eventId: string }>;
}) {
  const { orgSlug, eventId } = await params;
  const { ctx, organization } = await requireOrgContext(orgSlug);
  requireRole(ctx, ["VENDOR"]);
  requireVendor(ctx);

  const event = await prisma.event.findFirst({
    where: { id: eventId, ...tenantWhere(ctx) },
    include: { spaces: { orderBy: { price: "asc" } } },
  });
  if (!event) notFound();

  const application = await prisma.application.findUnique({
    where: {
      organizationId_vendorId_eventId: {
        organizationId: ctx.organizationId,
        vendorId: ctx.vendorId,
        eventId,
      },
    },
  });

  return (
    <div className="mx-auto w-full max-w-2xl flex-1 px-6 py-10">
      <h1 className="text-2xl font-semibold">{event.name}</h1>
      <p className="text-sm text-zinc-500">
        {event.startDate.toLocaleDateString()} – {event.endDate.toLocaleDateString()}
        {event.location ? ` · ${event.location}` : ""}
      </p>

      {!application && organization.vendorApplicationFee > 0 && (
        <Card className="mt-6">
          <p className="mb-3 text-sm text-zinc-600 dark:text-zinc-400">
            This market charges a nonrefundable application fee.
          </p>
          <ApplicationFeeCheckout
            orgSlug={orgSlug}
            eventId={eventId}
            fee={organization.vendorApplicationFee}
          />
        </Card>
      )}

      {!application && organization.vendorApplicationFee === 0 && (
        <Card className="mt-6">
          <p className="mb-3 text-sm text-zinc-600 dark:text-zinc-400">
            Submit your application to be considered for a space at this event.
          </p>
          <form action={submitApplicationAction.bind(null, orgSlug, eventId)}>
            <button
              type="submit"
              className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white dark:bg-white dark:text-zinc-900"
            >
              Submit application
            </button>
          </form>
        </Card>
      )}

      {application && application.status === "PENDING_FEE_PAYMENT" && (
        <Card className="mt-6">
          <Badge tone="yellow">Confirming your application fee payment…</Badge>
        </Card>
      )}

      {application && application.status === "SUBMITTED" && (
        <Card className="mt-6">
          <Badge tone="yellow">Application pending review</Badge>
        </Card>
      )}

      {application && application.status === "DENIED" && (
        <Card className="mt-6">
          <Badge tone="red">Application not approved</Badge>
        </Card>
      )}

      {application && application.status === "APPROVED" && (
        <>
          <h2 className="mt-8 text-lg font-semibold">Spaces</h2>
          <div className="mt-3 flex flex-col gap-2">
            {event.spaces.map((space) => (
              <Card key={space.id} className="flex items-center justify-between">
                <div>
                  <p className="font-medium">{space.name}</p>
                  <p className="text-sm text-zinc-500">
                    ${(space.price / 100).toFixed(2)} · {space.availableQty} left
                  </p>
                </div>
                {space.status === "AVAILABLE" && space.availableQty > 0 ? (
                  <Link
                    href={`/o/${orgSlug}/vendor/book/${space.id}`}
                    className="rounded-md bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white dark:bg-white dark:text-zinc-900"
                  >
                    Book
                  </Link>
                ) : (
                  <Badge tone="red">Sold out</Badge>
                )}
              </Card>
            ))}
            {event.spaces.length === 0 && (
              <p className="text-sm text-zinc-500">No spaces published yet.</p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
