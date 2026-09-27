import { strict as assert } from "node:assert";
import { test } from "node:test";

import {
  filtersToParams,
  formatDistance,
  formatOccurrence,
  normalizeBounds,
  parseArea,
  parseFilters,
  toApiParams,
  wrapLongitude,
} from "./logic.ts";

test("parseFilters keeps valid values and drops invalid ones", () => {
  const params = new URLSearchParams(
    "q=%20farm%20&type=POPUP&from=2026-10-01&to=2026-02-30&area=30,-100,45,-80",
  );
  assert.deepEqual(parseFilters(params), {
    q: "farm",
    type: "POPUP",
    from: "2026-10-01",
    to: "",
    area: { south: 30, west: -100, north: 45, east: -80 },
  });
  const bad = parseFilters(new URLSearchParams("type=CARNIVAL&area=50,0,40,10&from=soon"));
  assert.deepEqual(bad, { q: "", type: "", from: "", to: "", area: null });
});

test("filters round-trip through the URL and never include device location", () => {
  const filters = parseFilters(new URLSearchParams("q=night&type=FARMERS_MARKET&area=1,2,3,4"));
  const params = filtersToParams(filters);
  assert.equal(params.toString(), "q=night&type=FARMERS_MARKET&area=1%2C2%2C3%2C4");
  assert.deepEqual(parseFilters(params), filters);
  assert.ok(!params.has("lat") && !params.has("lng"));
});

test("toApiParams maps to Django parameter names", () => {
  const filters = parseFilters(new URLSearchParams("q=x&type=POPUP&from=2026-10-01&to=2026-10-31"));
  const params = toApiParams(filters, {
    near: { lat: 39.781712345, lng: -89.65, radiusKm: 25 },
    cursor: 20,
    limit: 20,
  });
  assert.equal(
    params.toString(),
    "q=x&market_type=POPUP&date_from=2026-10-01&date_to=2026-10-31&lat=39.7817&lng=-89.65&radius_km=25&cursor=20&limit=20",
  );
  const withArea = toApiParams({ ...filters, area: null }, { area: { south: 1, west: 2, north: 3, east: 4 } });
  assert.equal(withArea.get("south"), "1");
  assert.equal(withArea.get("east"), "4");
});

test("normalizeBounds wraps longitudes and handles world-wide views", () => {
  assert.deepEqual(normalizeBounds({ south: -10, west: 170, north: 10, east: 190 }), {
    south: -10,
    west: 170,
    north: 10,
    east: -170,
  });
  assert.deepEqual(normalizeBounds({ south: -95, west: -400, north: 95, east: 400 }), {
    south: -90,
    west: -180,
    north: 90,
    east: 180,
  });
  assert.equal(wrapLongitude(540), 180);
  assert.equal(wrapLongitude(-190), 170);
});

test("parseArea rejects malformed and out-of-range boxes", () => {
  assert.equal(parseArea("1,2,3"), null);
  assert.equal(parseArea("0,-190,10,0"), null);
  assert.equal(parseArea("a,b,c,d"), null);
  assert.deepEqual(parseArea("-40,170,0,-170"), { south: -40, west: 170, north: 0, east: -170 });
});

test("formatOccurrence shows the market's local time and zone", () => {
  const text = formatOccurrence({
    id: 1,
    starts_at: "2026-10-03T13:00:00Z",
    ends_at: "2026-10-03T18:00:00Z",
    timezone: "America/Chicago",
    local_date: "2026-10-03",
    local_start_time: "08:00:00",
    local_end_time: "13:00:00",
  });
  assert.equal(text, "Sat, Oct 3 · 8:00 AM – 1:00 PM CDT");
});

test("formatDistance uses kilometers", () => {
  assert.equal(formatDistance(0.94), "0.9 km away");
  assert.equal(formatDistance(18.6), "19 km away");
  assert.equal(formatDistance(null), null);
});
