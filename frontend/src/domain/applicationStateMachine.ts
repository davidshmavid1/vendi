import { prisma } from "@/lib/prisma";
import { writeAuditEvent } from "./audit";
import { eventDispatcher } from "./events/dispatcher";
import { InvalidTransitionError, NotFoundError, ForbiddenError } from "@/lib/errors";
import { requireRole, requireVendor, type AuthContext } from "@/lib/authz";
import type { ApplicationStatus } from "@/generated/prisma/enums";
import type { ApplicationModel, ApplicationWhereInput } from "@/generated/prisma/models";

/**
 * Application state machine. This is the ONLY place Application.status is
 * allowed to change — callers (Server Actions) never set `status` directly.
 * Centralizing the transition table here is what lets a future workflow
 * (e.g. a multi-step review) plug in without hunting down scattered
 * `if (status === ...)` checks across route handlers.
 */
const TRANSITIONS: Record<ApplicationStatus, ApplicationStatus[]> = {
  PENDING_FEE_PAYMENT: ["SUBMITTED"],
  SUBMITTED: ["APPROVED", "DENIED", "WITHDRAWN"],
  APPROVED: [],
  DENIED: [],
  WITHDRAWN: [],
};

function assertLegalTransition(from: ApplicationStatus, to: ApplicationStatus) {
  if (!TRANSITIONS[from]?.includes(to)) {
    throw new InvalidTransitionError(`Cannot move Application from ${from} to ${to}`);
  }
}

async function transition(params: {
  organizationId: string;
  applicationId: string;
  to: ApplicationStatus;
  actorId: string;
  extraWhere?: ApplicationWhereInput;
}): Promise<ApplicationModel> {
  const { organizationId, applicationId, to, actorId, extraWhere } = params;

  return prisma.$transaction(async (tx) => {
    const application = await tx.application.findFirst({
      where: { id: applicationId, organizationId, ...extraWhere },
    });
    if (!application) throw new NotFoundError("Application not found");

    assertLegalTransition(application.status, to);

    const isReview = to === "APPROVED" || to === "DENIED";
    const updated = await tx.application.update({
      where: { id: application.id },
      data: {
        status: to,
        reviewedAt: isReview ? new Date() : application.reviewedAt,
        reviewedBy: isReview ? actorId : application.reviewedBy,
      },
    });

    await writeAuditEvent(tx, {
      organizationId,
      entityType: "Application",
      entityId: application.id,
      action: `APPLICATION_${to}`,
      fromState: application.status,
      toState: to,
      actorId,
    });

    return updated;
  });
}

/**
 * Creates the Application row. If the org charges a vendor application fee
 * (`Organization.vendorApplicationFee > 0`), it starts life as
 * PENDING_FEE_PAYMENT and only becomes SUBMITTED once
 * confirmApplicationFeePayment (webhook-only) confirms payment — mirroring
 * exactly how Booking's PENDING_PAYMENT → CONFIRMED works. If the org doesn't
 * charge a fee, it goes straight to SUBMITTED, preserving the original
 * zero-friction behavior.
 */
export async function createPendingApplication(
  ctx: AuthContext,
  params: { eventId: string },
): Promise<ApplicationModel> {
  requireVendor(ctx);
  const { organizationId, vendorId } = ctx;
  const { eventId } = params;

  const { application, feeRequired } = await prisma.$transaction(async (tx) => {
    const event = await tx.event.findFirst({ where: { id: eventId, organizationId } });
    if (!event) throw new NotFoundError("Event not found");
    if (event.status !== "APPLICATIONS_OPEN") {
      throw new InvalidTransitionError("Applications are not open for this event");
    }

    const existing = await tx.application.findUnique({
      where: { organizationId_vendorId_eventId: { organizationId, vendorId, eventId } },
    });
    if (existing) {
      throw new InvalidTransitionError("An application for this event already exists");
    }

    const organization = await tx.organization.findFirstOrThrow({ where: { id: organizationId } });
    const feeRequired = organization.vendorApplicationFee > 0;
    const status = feeRequired ? "PENDING_FEE_PAYMENT" : "SUBMITTED";

    const created = await tx.application.create({
      data: { organizationId, vendorId, eventId, status },
    });

    await writeAuditEvent(tx, {
      organizationId,
      entityType: "Application",
      entityId: created.id,
      action: feeRequired ? "APPLICATION_PENDING_FEE_PAYMENT" : "APPLICATION_SUBMITTED",
      fromState: null,
      toState: status,
      actorId: ctx.userId,
    });

    return { application: created, feeRequired };
  });

  if (!feeRequired) {
    await eventDispatcher.emit({
      type: "APPLICATION_SUBMITTED",
      organizationId,
      applicationId: application.id,
      vendorId,
      eventId,
    });
  }

  return application;
}

/**
 * Webhook-only, mirrors confirmBookingPayment in bookingStateMachine.ts:
 * idempotent (a webhook retry against an already-SUCCEEDED Payment is a
 * no-op), transitions PENDING_FEE_PAYMENT → SUBMITTED inside one transaction
 * with the Payment update and the audit write. No inventory to touch, so
 * this is simpler than the booking version.
 */
export async function confirmApplicationFeePayment(params: {
  stripePaymentIntentId: string;
}): Promise<{ application: ApplicationModel; alreadyProcessed: boolean }> {
  const result = await prisma.$transaction(async (tx) => {
    const payment = await tx.payment.findUnique({
      where: { stripePaymentIntentId: params.stripePaymentIntentId },
    });
    if (!payment?.applicationId) {
      throw new NotFoundError("Application fee payment not found for this PaymentIntent");
    }

    if (payment.status === "SUCCEEDED") {
      const existingApplication = await tx.application.findUniqueOrThrow({
        where: { id: payment.applicationId },
      });
      return { application: existingApplication, alreadyProcessed: true };
    }

    const application = await tx.application.findFirst({
      where: { id: payment.applicationId, organizationId: payment.organizationId },
    });
    if (!application) throw new NotFoundError("Application not found");

    assertLegalTransition(application.status, "SUBMITTED");

    await tx.payment.update({ where: { id: payment.id }, data: { status: "SUCCEEDED" } });

    const updated = await tx.application.update({
      where: { id: application.id },
      data: { status: "SUBMITTED" },
    });

    await writeAuditEvent(tx, {
      organizationId: application.organizationId,
      entityType: "Application",
      entityId: application.id,
      action: "APPLICATION_SUBMITTED",
      fromState: application.status,
      toState: "SUBMITTED",
      actorId: null,
      payload: { stripePaymentIntentId: params.stripePaymentIntentId },
    });

    return { application: updated, alreadyProcessed: false };
  });

  if (!result.alreadyProcessed) {
    await eventDispatcher.emit({
      type: "APPLICATION_SUBMITTED",
      organizationId: result.application.organizationId,
      applicationId: result.application.id,
      vendorId: result.application.vendorId,
      eventId: result.application.eventId,
    });
  }

  return result;
}

export async function approveApplication(
  ctx: AuthContext,
  applicationId: string,
): Promise<ApplicationModel> {
  requireRole(ctx, ["ORGANIZER"]);

  const application = await transition({
    organizationId: ctx.organizationId,
    applicationId,
    to: "APPROVED",
    actorId: ctx.userId,
  });

  await eventDispatcher.emit({
    type: "APPLICATION_APPROVED",
    organizationId: ctx.organizationId,
    applicationId: application.id,
    vendorId: application.vendorId,
    eventId: application.eventId,
    reviewedBy: ctx.userId,
  });

  return application;
}

export async function denyApplication(
  ctx: AuthContext,
  applicationId: string,
): Promise<ApplicationModel> {
  requireRole(ctx, ["ORGANIZER"]);

  const application = await transition({
    organizationId: ctx.organizationId,
    applicationId,
    to: "DENIED",
    actorId: ctx.userId,
  });

  await eventDispatcher.emit({
    type: "APPLICATION_DENIED",
    organizationId: ctx.organizationId,
    applicationId: application.id,
    vendorId: application.vendorId,
    eventId: application.eventId,
    reviewedBy: ctx.userId,
  });

  return application;
}

export async function withdrawApplication(
  ctx: AuthContext,
  applicationId: string,
): Promise<ApplicationModel> {
  requireVendor(ctx);
  if (ctx.role !== "VENDOR") throw new ForbiddenError("Only a vendor can withdraw an application");

  const application = await transition({
    organizationId: ctx.organizationId,
    applicationId,
    to: "WITHDRAWN",
    actorId: ctx.userId,
    extraWhere: { vendorId: ctx.vendorId },
  });

  await eventDispatcher.emit({
    type: "APPLICATION_WITHDRAWN",
    organizationId: ctx.organizationId,
    applicationId: application.id,
    vendorId: application.vendorId,
    eventId: application.eventId,
  });

  return application;
}
