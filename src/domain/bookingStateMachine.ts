import { prisma } from "@/lib/prisma";
import { writeAuditEvent } from "./audit";
import { eventDispatcher } from "./events/dispatcher";
import { InvalidTransitionError, NotFoundError } from "@/lib/errors";
import { requireVendor, type AuthContext } from "@/lib/authz";
import type { BookingStatus } from "@/generated/prisma/enums";
import type { BookingModel } from "@/generated/prisma/models";

/**
 * Booking state machine. Like the Application machine, this is the only
 * place Booking.status changes. The one subtlety worth calling out is
 * confirmBookingPayment: it is never invoked from a client-facing action —
 * only from the signature-verified Stripe webhook handler — and it performs
 * the inventory decrement in the SAME transaction as the status flip, using
 * a conditional UPDATE so concurrent confirmations for the same space can't
 * oversell it.
 */
const TRANSITIONS: Record<BookingStatus, BookingStatus[]> = {
  PENDING_PAYMENT: ["CONFIRMED", "CANCELED", "EXPIRED"],
  CONFIRMED: ["CANCELED"],
  CANCELED: [],
  EXPIRED: [],
};

function assertLegalTransition(from: BookingStatus, to: BookingStatus) {
  if (!TRANSITIONS[from]?.includes(to)) {
    throw new InvalidTransitionError(`Cannot move Booking from ${from} to ${to}`);
  }
}

/**
 * Step 1 of the pipeline: hold a slot by creating a PENDING_PAYMENT booking.
 * Guards: vendor must hold an APPROVED application for the space's event,
 * and the space must currently have availability. Does NOT touch
 * availableQty — that only happens on confirmed payment, so an abandoned
 * checkout never leaks inventory.
 */
export async function createBooking(
  ctx: AuthContext,
  params: { spaceId: string },
): Promise<BookingModel> {
  requireVendor(ctx);
  const { organizationId, vendorId } = ctx;
  const { spaceId } = params;

  const booking = await prisma.$transaction(async (tx) => {
    const space = await tx.space.findFirst({ where: { id: spaceId, organizationId } });
    if (!space) throw new NotFoundError("Space not found");
    if (space.status !== "AVAILABLE" || space.availableQty <= 0) {
      throw new InvalidTransitionError("Space is not available for booking");
    }

    const application = await tx.application.findFirst({
      where: { organizationId, vendorId, eventId: space.eventId, status: "APPROVED" },
    });
    if (!application) {
      throw new InvalidTransitionError(
        "An approved application for this event is required before booking",
      );
    }

    const created = await tx.booking.create({
      data: {
        organizationId,
        vendorId,
        spaceId,
        applicationId: application.id,
        status: "PENDING_PAYMENT",
      },
    });

    await writeAuditEvent(tx, {
      organizationId,
      entityType: "Booking",
      entityId: created.id,
      action: "BOOKING_CREATED",
      fromState: null,
      toState: "PENDING_PAYMENT",
      actorId: ctx.userId,
    });

    return created;
  });

  await eventDispatcher.emit({
    type: "BOOKING_CREATED",
    organizationId,
    bookingId: booking.id,
    vendorId,
    spaceId,
  });

  return booking;
}

/**
 * Step 2 of the pipeline, driven exclusively by the Stripe webhook. Confirms
 * payment, flips the booking to CONFIRMED, and atomically decrements
 * Space.availableQty via a conditional UPDATE (`WHERE availableQty > 0`) —
 * Postgres row-locks during that UPDATE, so two concurrent confirmations for
 * the last unit of a space can't both succeed. Idempotent: a webhook retry
 * for an already-SUCCEEDED payment is a no-op.
 */
export async function confirmBookingPayment(params: {
  stripePaymentIntentId: string;
}): Promise<{ booking: BookingModel; spaceSoldOut: boolean; alreadyProcessed: boolean }> {
  const result = await prisma.$transaction(async (tx) => {
    const payment = await tx.payment.findUnique({
      where: { stripePaymentIntentId: params.stripePaymentIntentId },
    });
    if (!payment?.bookingId) throw new NotFoundError("Booking payment not found for this PaymentIntent");

    if (payment.status === "SUCCEEDED") {
      const existingBooking = await tx.booking.findUniqueOrThrow({
        where: { id: payment.bookingId },
      });
      return { booking: existingBooking, spaceSoldOut: false, alreadyProcessed: true };
    }

    const booking = await tx.booking.findFirst({
      where: { id: payment.bookingId, organizationId: payment.organizationId },
    });
    if (!booking) throw new NotFoundError("Booking not found");

    assertLegalTransition(booking.status, "CONFIRMED");

    await tx.payment.update({ where: { id: payment.id }, data: { status: "SUCCEEDED" } });

    const updatedBooking = await tx.booking.update({
      where: { id: booking.id },
      data: { status: "CONFIRMED", confirmedAt: new Date() },
    });

    const decremented = await tx.space.updateMany({
      where: { id: booking.spaceId, availableQty: { gt: 0 } },
      data: { availableQty: { decrement: 1 } },
    });
    if (decremented.count === 0) {
      // Someone else claimed the last unit between our earlier checks and
      // now. Surface this loudly rather than confirming an oversold slot.
      throw new InvalidTransitionError("Space sold out during payment confirmation");
    }

    const space = await tx.space.findUniqueOrThrow({ where: { id: booking.spaceId } });
    let spaceSoldOut = false;
    if (space.availableQty === 0 && space.status !== "SOLD_OUT") {
      await tx.space.update({ where: { id: space.id }, data: { status: "SOLD_OUT" } });
      spaceSoldOut = true;
    }

    await writeAuditEvent(tx, {
      organizationId: booking.organizationId,
      entityType: "Booking",
      entityId: booking.id,
      action: "BOOKING_CONFIRMED",
      fromState: booking.status,
      toState: "CONFIRMED",
      actorId: null,
      payload: { stripePaymentIntentId: params.stripePaymentIntentId },
    });

    if (spaceSoldOut) {
      await writeAuditEvent(tx, {
        organizationId: booking.organizationId,
        entityType: "Space",
        entityId: space.id,
        action: "SPACE_SOLD_OUT",
        fromState: "AVAILABLE",
        toState: "SOLD_OUT",
        actorId: null,
      });
    }

    return { booking: updatedBooking, spaceSoldOut, alreadyProcessed: false };
  });

  if (!result.alreadyProcessed) {
    await eventDispatcher.emit({
      type: "BOOKING_CONFIRMED",
      organizationId: result.booking.organizationId,
      bookingId: result.booking.id,
      vendorId: result.booking.vendorId,
      spaceId: result.booking.spaceId,
      spaceSoldOut: result.spaceSoldOut,
    });
  }

  return result;
}

/** Organizer can cancel any booking in-org; a vendor can cancel only their own. */
export async function cancelBooking(ctx: AuthContext, bookingId: string): Promise<BookingModel> {
  const extraWhere = ctx.role === "VENDOR" ? { vendorId: ctx.vendorId ?? "__none__" } : {};

  const result = await prisma.$transaction(async (tx) => {
    const booking = await tx.booking.findFirst({
      where: { id: bookingId, organizationId: ctx.organizationId, ...extraWhere },
    });
    if (!booking) throw new NotFoundError("Booking not found");

    assertLegalTransition(booking.status, "CANCELED");
    const wasConfirmed = booking.status === "CONFIRMED";

    const updated = await tx.booking.update({
      where: { id: booking.id },
      data: { status: "CANCELED", canceledAt: new Date() },
    });

    if (wasConfirmed) {
      await tx.space.update({
        where: { id: booking.spaceId },
        data: { availableQty: { increment: 1 }, status: "AVAILABLE" },
      });
    }

    await writeAuditEvent(tx, {
      organizationId: booking.organizationId,
      entityType: "Booking",
      entityId: booking.id,
      action: "BOOKING_CANCELED",
      fromState: booking.status,
      toState: "CANCELED",
      actorId: ctx.userId,
    });

    return { booking: updated, previousStatus: booking.status };
  });

  await eventDispatcher.emit({
    type: "BOOKING_CANCELED",
    organizationId: ctx.organizationId,
    bookingId: result.booking.id,
    vendorId: result.booking.vendorId,
    spaceId: result.booking.spaceId,
    previousStatus: result.previousStatus,
  });

  return result.booking;
}
