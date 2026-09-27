import { prisma } from "@/lib/prisma";
import { emailService } from "@/lib/email";
import { eventDispatcher } from "./dispatcher";

/**
 * Vendor accounts are created at vendor signup (see src/server/actions/vendors.ts),
 * so "provision vendor account" on approval doesn't create a new row — the
 * thing being provisioned is *booking access* for this event, which the
 * booking state machine already enforces by requiring an APPROVED
 * application. What's left as a side effect here is purely notifying the
 * vendor that they're clear to book.
 */
eventDispatcher.on("APPLICATION_APPROVED", async (event) => {
  const application = await prisma.application.findUnique({
    where: { id: event.applicationId },
    include: { vendor: { include: { user: true } }, event: true },
  });
  if (!application) return;

  await emailService.send({
    to: application.vendor.user.email,
    subject: `You're approved for ${application.event.name}`,
    body: `Hi ${application.vendor.businessName}, your application for ${application.event.name} was approved. Log in to your vendor dashboard to book your space.`,
  });
});

eventDispatcher.on("APPLICATION_DENIED", async (event) => {
  const application = await prisma.application.findUnique({
    where: { id: event.applicationId },
    include: { vendor: { include: { user: true } }, event: true },
  });
  if (!application) return;

  await emailService.send({
    to: application.vendor.user.email,
    subject: `Update on your application to ${application.event.name}`,
    body: `Hi ${application.vendor.businessName}, your application for ${application.event.name} was not approved this time.`,
  });
});

eventDispatcher.on("BOOKING_CONFIRMED", async (event) => {
  const booking = await prisma.booking.findUnique({
    where: { id: event.bookingId },
    include: { vendor: { include: { user: true } }, space: { include: { event: true } } },
  });
  if (!booking) return;

  await emailService.send({
    to: booking.vendor.user.email,
    subject: `Booking confirmed: ${booking.space.name}`,
    body: `Hi ${booking.vendor.businessName}, your booking for ${booking.space.name} at ${booking.space.event.name} is confirmed.`,
  });

  if (event.spaceSoldOut) {
    // Phase 2: this is where we'd notify the next WaitlistEntry by position
    // and start its claim-expiry timer via a queue. The WaitlistEntry model
    // and SOLD_OUT status already exist for that; only the timer-driven
    // automation is deferred.
    console.log(`[events] space ${event.spaceId} sold out`);
  }
});

export {};
