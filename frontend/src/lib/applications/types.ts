// Response shapes of the Django application endpoints (backend/applications/schemas.py).
import type { ApplicationStatus, IntakeState, Question } from "./logic";

export type VendorRole = "OWNER" | "MEMBER";

export type MyBusiness = {
  business: { id: number; name: string; category: string; city: string; region: string };
  membership_id: number;
  role: VendorRole;
};

export type Page<T> = { items: T[]; next_cursor: number | null };

export type PublicOccurrence = {
  id: number;
  market_id: number;
  starts_at: string;
  ends_at: string;
  timezone: string;
  local_date: string;
  local_start_time: string;
  local_end_time: string;
  status: "SCHEDULED" | "CANCELLED";
  cancellation_message: string;
};

export type PublicIntake = {
  occurrence: PublicOccurrence;
  market_name: string;
  organizer_name: string;
  state: IntakeState;
  opens_at: string | null;
  closes_at: string | null;
  instructions: string;
  questions_version: number | null;
  questions: Question[];
};

export type ApplicationOccurrence = {
  id: number;
  market_id: number;
  market_name: string;
  starts_at: string;
  ends_at: string;
  timezone: string;
  status: string;
};

export type VendorSnapshot = {
  name: string;
  category: string;
  description: string;
  contact_email: string;
  phone: string;
  website: string;
  city: string;
  region: string;
};

type ApplicationFields = {
  id: number;
  status: ApplicationStatus;
  vendor_business_id: number;
  occurrence: ApplicationOccurrence;
  questions_version: number;
  questions: Question[];
  answers: Record<string, string | boolean>;
  vendor_snapshot: VendorSnapshot;
  submitted_at: string;
  decided_at: string | null;
  decision_message: string;
  withdrawn_at: string | null;
};

export type VendorApplication = ApplicationFields & { organizer_name: string };

export type OrganizerApplication = ApplicationFields & {
  submitted_by_user_id: number;
  decided_by_user_id: number | null;
  withdrawn_by_user_id: number | null;
  history: { from_status: string; to_status: ApplicationStatus; actor_user_id: number; at: string }[];
};

export type OrganizerApplicationSummary = {
  id: number;
  status: ApplicationStatus;
  vendor_business_id: number;
  vendor_name: string;
  occurrence: ApplicationOccurrence;
  submitted_at: string;
  decided_at: string | null;
};

export type OrganizationRole = "OWNER" | "ADMIN" | "STAFF";

export type MyOrganization = {
  organization: { id: number; name: string; slug: string };
  membership_id: number;
  role: OrganizationRole;
};

export const CATEGORIES: Record<string, string> = {
  PRODUCE: "Produce",
  MEAT_DAIRY_EGGS: "Meat, dairy & eggs",
  BAKED_GOODS: "Baked goods",
  PREPARED_FOOD: "Prepared food",
  BEVERAGES: "Beverages",
  CRAFTS: "Crafts & art",
  FLOWERS_PLANTS: "Flowers & plants",
  HEALTH_BEAUTY: "Health & beauty",
  OTHER: "Other",
};
