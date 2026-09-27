"use server";

import { z } from "zod";
import { revalidatePath } from "next/cache";
import { prisma } from "@/lib/prisma";
import { requireOrgContext, requireRole } from "@/lib/authz";
import { writeAuditEvent } from "@/domain/audit";

export type ActionState = { error?: string };

const setApplicationFeeSchema = z.object({
  vendorApplicationFee: z.coerce.number().int().min(0),
});

export async function setVendorApplicationFee(
  orgSlug: string,
  _prevState: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const { ctx } = await requireOrgContext(orgSlug);
  requireRole(ctx, ["ORGANIZER"]);

  const parsed = setApplicationFeeSchema.safeParse({
    vendorApplicationFee: formData.get("vendorApplicationFee"),
  });
  if (!parsed.success) return { error: parsed.error.issues[0]?.message ?? "Invalid input" };

  await prisma.$transaction(async (tx) => {
    const organization = await tx.organization.findFirstOrThrow({
      where: { id: ctx.organizationId },
    });
    const updated = await tx.organization.update({
      where: { id: organization.id },
      data: { vendorApplicationFee: parsed.data.vendorApplicationFee },
    });
    await writeAuditEvent(tx, {
      organizationId: ctx.organizationId,
      entityType: "Organization",
      entityId: organization.id,
      action: "VENDOR_APPLICATION_FEE_UPDATED",
      actorId: ctx.userId,
      payload: {
        from: organization.vendorApplicationFee,
        to: updated.vendorApplicationFee,
      },
    });
  });

  revalidatePath(`/o/${orgSlug}/dashboard/settings/application-fee`);
  return {};
}
