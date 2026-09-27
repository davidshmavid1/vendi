"use server";

import { z } from "zod";
import { revalidatePath } from "next/cache";
import { prisma } from "@/lib/prisma";
import { requireOrgContext, requireRole } from "@/lib/authz";
import { writeAuditEvent } from "@/domain/audit";

const createEventSchema = z.object({
  name: z.string().min(2).max(150),
  description: z.string().max(2000).optional(),
  location: z.string().max(200).optional(),
  startDate: z.string().min(1),
  endDate: z.string().min(1),
});

export type ActionState = { error?: string };

export async function createEvent(
  orgSlug: string,
  _prevState: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const { ctx } = await requireOrgContext(orgSlug);
  requireRole(ctx, ["ORGANIZER"]);

  const parsed = createEventSchema.safeParse({
    name: formData.get("name"),
    description: formData.get("description") || undefined,
    location: formData.get("location") || undefined,
    startDate: formData.get("startDate"),
    endDate: formData.get("endDate"),
  });
  if (!parsed.success) return { error: parsed.error.issues[0]?.message ?? "Invalid input" };

  const startDate = new Date(parsed.data.startDate);
  const endDate = new Date(parsed.data.endDate);
  if (Number.isNaN(startDate.getTime()) || Number.isNaN(endDate.getTime()) || endDate < startDate) {
    return { error: "Invalid event dates" };
  }

  await prisma.$transaction(async (tx) => {
    const created = await tx.event.create({
      data: {
        organizationId: ctx.organizationId,
        name: parsed.data.name,
        description: parsed.data.description,
        location: parsed.data.location,
        startDate,
        endDate,
      },
    });
    await writeAuditEvent(tx, {
      organizationId: ctx.organizationId,
      entityType: "Event",
      entityId: created.id,
      action: "EVENT_CREATED",
      toState: created.status,
      actorId: ctx.userId,
    });
  });

  revalidatePath(`/o/${orgSlug}/dashboard/events`);
  return {};
}

export async function setEventApplicationsOpen(orgSlug: string, eventId: string, open: boolean) {
  const { ctx } = await requireOrgContext(orgSlug);
  requireRole(ctx, ["ORGANIZER"]);

  await prisma.$transaction(async (tx) => {
    const event = await tx.event.findFirst({
      where: { id: eventId, organizationId: ctx.organizationId },
    });
    if (!event) return;

    const toState = open ? "APPLICATIONS_OPEN" : "APPLICATIONS_CLOSED";
    if (event.status === toState) return;

    const updated = await tx.event.update({ where: { id: event.id }, data: { status: toState } });
    await writeAuditEvent(tx, {
      organizationId: ctx.organizationId,
      entityType: "Event",
      entityId: event.id,
      action: `EVENT_${toState}`,
      fromState: event.status,
      toState: updated.status,
      actorId: ctx.userId,
    });
  });

  revalidatePath(`/o/${orgSlug}/dashboard/events/${eventId}`);
}
