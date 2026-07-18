import type { Prisma } from "@/generated/prisma/client";

/**
 * Append-only audit log write. Always called from inside the same
 * transaction as the state mutation it's recording, so the audit trail and
 * the change it describes commit or roll back together — this is the
 * source of truth Phase 3 analytics will read from.
 */
export async function writeAuditEvent(
  tx: Prisma.TransactionClient,
  params: {
    organizationId: string;
    entityType: string;
    entityId: string;
    action: string;
    fromState?: string | null;
    toState?: string | null;
    actorId?: string | null;
    payload?: Prisma.InputJsonObject;
  },
) {
  await tx.auditEvent.create({
    data: {
      organizationId: params.organizationId,
      entityType: params.entityType,
      entityId: params.entityId,
      action: params.action,
      fromState: params.fromState ?? null,
      toState: params.toState ?? null,
      actorId: params.actorId ?? null,
      payload: params.payload ?? undefined,
    },
  });
}
