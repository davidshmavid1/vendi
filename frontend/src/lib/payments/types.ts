// Response shapes of backend/payments/schemas.py.
import type { Reservation } from "@/lib/reservations/types";

export type PaymentStateName =
  | "HOLDING"
  | "CHECKOUT_OPEN"
  | "PROCESSING"
  | "BOOKED"
  | "REFUND_PENDING"
  | "REFUNDED"
  | "REFUND_FAILED"
  | "EXPIRED"
  | "RELEASED";

export type Booking = {
  id: number;
  reservation_id: number;
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
