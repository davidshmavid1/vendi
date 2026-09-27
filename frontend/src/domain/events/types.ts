import type { ApplicationStatus, BookingStatus } from "@/generated/prisma/enums";

/**
 * Domain events published *after* a state transition has committed. Handlers
 * react to these for side effects that must NOT be atomic with the DB write
 * that caused them (sending an email, provisioning a resource) — anything
 * that must be atomic (inventory decrement, audit log) happens inside the
 * state machine's own transaction instead. See src/domain/audit.ts.
 *
 * Swapping the in-process dispatcher (src/domain/events/dispatcher.ts) for a
 * durable queue (BullMQ/Graphile Worker, Phase 2) only means changing how
 * these events get from `emit` to a handler — the event shapes and handler
 * functions themselves don't change.
 */
export type DomainEvent =
  | {
      type: "APPLICATION_SUBMITTED";
      organizationId: string;
      applicationId: string;
      vendorId: string;
      eventId: string;
    }
  | {
      type: "APPLICATION_APPROVED";
      organizationId: string;
      applicationId: string;
      vendorId: string;
      eventId: string;
      reviewedBy: string;
    }
  | {
      type: "APPLICATION_DENIED";
      organizationId: string;
      applicationId: string;
      vendorId: string;
      eventId: string;
      reviewedBy: string;
    }
  | {
      type: "APPLICATION_WITHDRAWN";
      organizationId: string;
      applicationId: string;
      vendorId: string;
      eventId: string;
    }
  | {
      type: "BOOKING_CREATED";
      organizationId: string;
      bookingId: string;
      vendorId: string;
      spaceId: string;
    }
  | {
      type: "BOOKING_CONFIRMED";
      organizationId: string;
      bookingId: string;
      vendorId: string;
      spaceId: string;
      spaceSoldOut: boolean;
    }
  | {
      type: "BOOKING_CANCELED";
      organizationId: string;
      bookingId: string;
      vendorId: string;
      spaceId: string;
      previousStatus: BookingStatus;
    }
  | {
      type: "BOOKING_EXPIRED";
      organizationId: string;
      bookingId: string;
      vendorId: string;
      spaceId: string;
    };

export type DomainEventType = DomainEvent["type"];

export type ApplicationTransitionMeta = {
  fromState: ApplicationStatus;
  toState: ApplicationStatus;
};

export type BookingTransitionMeta = {
  fromState: BookingStatus;
  toState: BookingStatus;
};
