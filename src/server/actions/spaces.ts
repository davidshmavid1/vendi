"use server";

import { z } from "zod";
import { revalidatePath } from "next/cache";
import { prisma } from "@/lib/prisma";
import { requireOrgContext, requireRole } from "@/lib/authz";
import { writeAuditEvent } from "@/domain/audit";
import { NotFoundError } from "@/lib/errors";

const createSpaceSchema = z.object({
  eventId: z.string().min(1),
  name: z.string().min(1).max(150),
  description: z.string().max(2000).optional(),
  price: z.coerce.number().int().min(0),
  totalQty: z.coerce.number().int().min(1),
});

export type ActionState = { error?: string };

export async function createSpace(
  orgSlug: string,
  _prevState: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const { ctx } = await requireOrgContext(orgSlug);
  requireRole(ctx, ["ORGANIZER"]);

  const parsed = createSpaceSchema.safeParse({
    eventId: formData.get("eventId"),
    name: formData.get("name"),
    description: formData.get("description") || undefined,
    price: formData.get("price"),
    totalQty: formData.get("totalQty"),
  });
  if (!parsed.success) return { error: parsed.error.issues[0]?.message ?? "Invalid input" };

  const { eventId, name, description, price, totalQty } = parsed.data;

  await prisma.$transaction(async (tx) => {
    const event = await tx.event.findFirst({ where: { id: eventId, organizationId: ctx.organizationId } });
    if (!event) throw new NotFoundError("Event not found");

    const created = await tx.space.create({
      data: {
        organizationId: ctx.organizationId,
        eventId,
        name,
        description,
        price,
        totalQty,
        availableQty: totalQty,
      },
    });

    await writeAuditEvent(tx, {
      organizationId: ctx.organizationId,
      entityType: "Space",
      entityId: created.id,
      action: "SPACE_CREATED",
      toState: created.status,
      actorId: ctx.userId,
      payload: { price, totalQty },
    });
  });

  revalidatePath(`/o/${orgSlug}/dashboard/events/${eventId}`);
  return {};
}
