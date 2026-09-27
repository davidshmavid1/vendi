// Response shapes of backend/layouts/schemas.py.

export type PhysicalUnit = "FT" | "M";

export type StallOut = {
  id: number;
  label: string;
  description: string;
  x: number;
  y: number;
  width: number;
  height: number;
  physical_width: string | null;
  physical_depth: string | null;
  physical_unit: PhysicalUnit | null;
};

/** A market's layout version: the physical plan. Locked once a date uses it. */
export type VersionOut = {
  id: number;
  market_id: number;
  number: number;
  canvas_width: number;
  canvas_height: number;
  revision: number;
  locked: boolean;
  locked_at: string | null;
  based_on_id: number | null;
  stalls: StallOut[];
};

export type VersionSummary = {
  id: number;
  number: number;
  canvas_width: number;
  canvas_height: number;
  revision: number;
  locked: boolean;
  based_on_id: number | null;
  stall_count: number;
  date_count: number;
};

export type OfferOut = { id: number; stall_id: number; price_minor: number; currency: string; enabled: boolean };

/** One date's selected version and its offers (prices). */
export type DateLayoutOut = {
  occurrence_id: number;
  market_id: number;
  layout_version: VersionOut;
  currency: string | null;
  currency_exponent: number | null;
  revision: number;
  published: boolean;
  published_at: string | null;
  offers: OfferOut[];
};

export type PublicStall = StallOut & { offer_id: number | null; offered: boolean; price_minor: number | null };

export type PublicLayout = {
  occurrence_id: number;
  market_id: number;
  canvas_width: number;
  canvas_height: number;
  currency: string;
  currency_exponent: number;
  stalls: PublicStall[];
};

export const UNIT_LABELS: Record<PhysicalUnit, string> = { FT: "ft", M: "m" };

export function physicalSize(s: { physical_width: string | null; physical_depth: string | null; physical_unit: PhysicalUnit | null }) {
  if (!s.physical_width || !s.physical_depth || !s.physical_unit) return null;
  const trim = (v: string) => (v.includes(".") ? v.replace(/\.?0+$/, "") : v);
  return `${trim(s.physical_width)} × ${trim(s.physical_depth)} ${UNIT_LABELS[s.physical_unit]}`;
}
