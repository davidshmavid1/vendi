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
  enabled: boolean;
  price_minor: number;
};

export type LayoutOut = {
  id: number;
  occurrence_id: number;
  market_id: number;
  canvas_width: number;
  canvas_height: number;
  currency: string;
  currency_exponent: number;
  revision: number;
  published: boolean;
  published_at: string | null;
  stalls: StallOut[];
};

export type PublicStall = Omit<StallOut, "enabled"> & { offered: boolean };

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
