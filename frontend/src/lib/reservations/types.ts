// Response shapes of backend/reservations/schemas.py.

export type ReservationStatus = "HELD" | "EXPIRED" | "RELEASED" | "CONFIRMED";

export type Reservation = {
  id: number;
  status: ReservationStatus;
  offer_id: number;
  stall: { id: number; label: string };
  occurrence: { id: number; market_id: number; market_name: string; starts_at: string; ends_at: string; timezone: string };
  application_id: number;
  price_minor: number;
  currency: string;
  currency_exponent: number;
  expires_at: string;
  created_at: string;
  expired_at: string | null;
  released_at: string | null;
  confirmed_at: string | null;
  server_time: string;
};

export type StallAvailability = { stall_id: number; offer_id: number | null; status: "available" | "unavailable" | "not_offered" };

export type Availability = { occurrence_id: number; items: StallAvailability[] };
