// Response shapes of backend/payments/schemas.py.
import type { Reservation } from "@/lib/reservations/types";

export type PaymentStateName =
  | "HOLDING"
  | "CANCELLED"
  | "CHECKOUT_OPEN"
  | "PROCESSING"
  | "BOOKED"
  | "REFUND_PENDING"
  | "REFUNDED"
  | "REFUND_FAILED"
  | "EXPIRED"
  | "RELEASED";

export type RefundStatus = "REQUESTED" | "PENDING" | "SUCCEEDED" | "FAILED" | "CANCELED" | "REVIEW";

export type Booking = {
  id: number;
  reservation_id: number;
  // Booking state only; money is in `refund`.
  status: "CONFIRMED" | "CANCELLED";
  paid_minor: number;
  fee_minor: number;
  cancelled_at: string | null;
  terms: { captured: boolean; vendor_cutoff_hours: number | null; vendor_deadline: string | null };
  cancellation: {
    kind: "VENDOR" | "ORGANIZER" | "EVENT";
    reason: string;
    requested_at: string;
    completed_at: string;
    refund_rule: "PAID_MINUS_FEE" | "NO_PAYMENT";
    refund_entitlement_minor: number;
    internal_note: string | null;
  } | null;
  refund: { status: RefundStatus; amount_minor: number; currency: string; completed_at: string | null } | null;
  stall: { id: number; label: string };
  occurrence: Reservation["occurrence"];
  vendor_business: { id: number; name: string };
  price_minor: number;
  currency: string;
  currency_exponent: number;
  payment_required: boolean;
  paid_at: string | null;
  created_at: string;
};

export type CheckoutQuote = {
  stall_price_minor: number;
  fee_minor: number;
  total_minor: number;
  currency: string;
  currency_exponent: number;
};

export type PaymentStatus = {
  state: PaymentStateName;
  payment_required: boolean;
  quote: CheckoutQuote | null;
  reservation: Reservation;
  payment: {
    id: number;
    status: "CREATING" | "OPEN" | "SUCCEEDED" | "EXPIRED" | "CANCELED" | "FAILED";
    fulfillment: "FULFILLED" | "UNFULFILLED" | null;
    // Total charged = stall price + Vendi's fee (added on top).
    amount_minor: number;
    stall_price_minor: number;
    fee_minor: number;
    currency: string;
    currency_exponent: number;
    session_expires_at: string;
    succeeded_at: string | null;
    checkout_url: string | null;
  } | null;
  booking: Booking | null;
  refund: { status: string; amount_minor: number; currency: string } | null;
};

export type BookingPage = { items: Booking[]; next_cursor: number | null };

export type CancellationPreview = {
  booking: Booking;
  permitted: boolean;
  problem: string | null;
  message: string | null;
  can_act: boolean;
  refund_rule: "PAID_MINUS_FEE" | "NO_PAYMENT";
  paid_minor: number;
  fee_retained_minor: number;
  refund_minor: number;
  currency: string;
  currency_exponent: number;
  vendor_deadline: string | null;
  releases_stall: boolean;
  server_time: string;
};

export type DateCancellation = {
  occurrence_id: number;
  cancelled: boolean;
  requested_at: string | null;
  completed_at: string | null;
  items: Record<string, Record<string, number>>;
  refunds: Record<string, number>;
  pending: boolean;
};
