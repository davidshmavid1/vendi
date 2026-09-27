// Pure helpers for market discovery: URL <-> filters, map bounds, display
// formatting. No React or Next imports, so they run under `node --test`.

export type MarketType = "FARMERS_MARKET" | "POPUP";

export type Bounds = { south: number; west: number; north: number; east: number };

export type Near = { lat: number; lng: number; radiusKm: number };

export type DiscoveryFilters = {
  q: string;
  type: MarketType | "";
  from: string; // YYYY-MM-DD, market-local calendar date
  to: string;
  area: Bounds | null; // the applied "Search this area"
};

export type DiscoveryOccurrence = {
  id: number;
  starts_at: string;
  ends_at: string;
  timezone: string;
  local_date: string;
  local_start_time: string;
  local_end_time: string;
};

export const MARKET_TYPES: Record<MarketType, string> = {
  FARMERS_MARKET: "Farmers market",
  POPUP: "Popup",
};

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const MAX_QUERY = 100;

function isValidDate(value: string): boolean {
  if (!DATE_PATTERN.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().startsWith(value);
}

function round(value: number): number {
  return Math.round(value * 10_000) / 10_000;
}

export function wrapLongitude(lng: number): number {
  const wrapped = ((((lng + 180) % 360) + 360) % 360) - 180;
  return wrapped === -180 && lng > 0 ? 180 : wrapped;
}

/** Normalize a map viewport to the API's box: latitudes clamped to ±90,
 * longitudes wrapped to ±180. A box wider than the world becomes the whole
 * world; east < west means it crosses the antimeridian. */
export function normalizeBounds(raw: Bounds): Bounds {
  const south = Math.max(-90, Math.min(90, raw.south));
  const north = Math.max(-90, Math.min(90, raw.north));
  if (raw.east - raw.west >= 360) {
    return { south: round(south), west: -180, north: round(north), east: 180 };
  }
  return {
    south: round(south),
    west: round(wrapLongitude(raw.west)),
    north: round(north),
    east: round(wrapLongitude(raw.east)),
  };
}

export function parseArea(value: string | null): Bounds | null {
  if (!value) return null;
  const parts = value.split(",").map(Number);
  if (parts.length !== 4 || parts.some((n) => !Number.isFinite(n))) return null;
  const [south, west, north, east] = parts;
  if (south < -90 || north > 90 || south > north) return null;
  if (Math.abs(west) > 180 || Math.abs(east) > 180) return null;
  return { south, west, north, east };
}

export function formatArea(area: Bounds): string {
  return [area.south, area.west, area.north, area.east].map(round).join(",");
}

export function parseFilters(params: URLSearchParams): DiscoveryFilters {
  const type = params.get("type");
  const from = params.get("from") ?? "";
  const to = params.get("to") ?? "";
  return {
    q: (params.get("q") ?? "").trim().slice(0, MAX_QUERY),
    type: type === "FARMERS_MARKET" || type === "POPUP" ? type : "",
    from: isValidDate(from) ? from : "",
    to: isValidDate(to) ? to : "",
    area: parseArea(params.get("area")),
  };
}

/** Page URL parameters. Device location is never included. */
export function filtersToParams(filters: DiscoveryFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  if (filters.type) params.set("type", filters.type);
  if (filters.from) params.set("from", filters.from);
  if (filters.to) params.set("to", filters.to);
  if (filters.area) params.set("area", formatArea(filters.area));
  return params;
}

/** Django query parameters for list or map requests. */
export function toApiParams(
  filters: DiscoveryFilters,
  options: { near?: Near | null; area?: Bounds | null; cursor?: number | null; limit?: number } = {},
): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  if (filters.type) params.set("market_type", filters.type);
  if (filters.from) params.set("date_from", filters.from);
  if (filters.to) params.set("date_to", filters.to);
  const area = options.area === undefined ? filters.area : options.area;
  if (area) {
    params.set("south", String(area.south));
    params.set("west", String(area.west));
    params.set("north", String(area.north));
    params.set("east", String(area.east));
  }
  if (options.near) {
    params.set("lat", String(round(options.near.lat)));
    params.set("lng", String(round(options.near.lng)));
    params.set("radius_km", String(options.near.radiusKm));
  }
  if (options.cursor) params.set("cursor", String(options.cursor));
  if (options.limit) params.set("limit", String(options.limit));
  return params;
}

/** "Sat, Oct 3 · 8:00 AM – 1:00 PM CDT", in the market's own timezone. */
export function formatOccurrence(occurrence: DiscoveryOccurrence, locale = "en-US"): string {
  const zone = occurrence.timezone;
  const day = new Intl.DateTimeFormat(locale, {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: zone,
  }).format(new Date(occurrence.starts_at));
  const time = (iso: string, withZone: boolean) =>
    new Intl.DateTimeFormat(locale, {
      hour: "numeric",
      minute: "2-digit",
      timeZone: zone,
      ...(withZone ? { timeZoneName: "short" } : {}),
    }).format(new Date(iso));
  return `${day} · ${time(occurrence.starts_at, false)} – ${time(occurrence.ends_at, true)}`;
}

export function formatDistance(km: number | null | undefined): string | null {
  if (km === null || km === undefined) return null;
  return km < 10 ? `${km.toFixed(1)} km away` : `${Math.round(km)} km away`;
}
