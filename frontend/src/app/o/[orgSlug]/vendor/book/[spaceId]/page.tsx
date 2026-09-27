import { notFound } from "next/navigation";
import { requireOrgContext, requireRole, requireVendor, tenantWhere } from "@/lib/authz";
import { prisma } from "@/lib/prisma";
import { Card } from "@/components/ui";
import { BookingCheckout } from "@/components/forms/BookingCheckout";

export default async function BookSpacePage({
  params,
}: {
  params: Promise<{ orgSlug: string; spaceId: string }>;
}) {
  const { orgSlug, spaceId } = await params;
  const { ctx } = await requireOrgContext(orgSlug);
  requireRole(ctx, ["VENDOR"]);
  requireVendor(ctx);

  const space = await prisma.space.findFirst({
    where: { id: spaceId, ...tenantWhere(ctx) },
    include: { event: true },
  });
  if (!space) notFound();

  return (
    <div className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center px-6 py-16">
      <h1 className="text-2xl font-semibold">{space.name}</h1>
      <p className="text-sm text-zinc-500">{space.event.name}</p>
      <Card className="mt-6">
        <BookingCheckout orgSlug={orgSlug} spaceId={space.id} price={space.price} />
      </Card>
    </div>
  );
}
