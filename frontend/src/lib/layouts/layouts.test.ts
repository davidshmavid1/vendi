import { test } from "node:test";
import assert from "node:assert/strict";
import { formatMinor, inputToMinor, minorToInput } from "./money.ts";
import { clampPosition, fits, freeSpot, overlaps } from "./geometry.ts";

test("formatMinor respects the currency exponent", () => {
  assert.equal(formatMinor(2500, "USD", 2), "$25.00");
  assert.equal(formatMinor(5, "USD", 2), "$0.05");
  assert.equal(formatMinor(123456789, "USD", 2), "$1,234,567.89");
  assert.equal(formatMinor(2500, "JPY", 0), "¥2,500");
  assert.equal(formatMinor(0, "EUR", 2), "€0.00");
});

test("inputToMinor parses exactly, without floats", () => {
  assert.equal(inputToMinor("12.5", 2), 1250);
  assert.equal(inputToMinor("0.29", 2), 29); // 0.29 * 100 is 28.999... as a float
  assert.equal(inputToMinor("1,234.05", 2), 123405);
  assert.equal(inputToMinor("2500", 0), 2500);
  assert.equal(inputToMinor("25.5", 0), null);
  assert.equal(inputToMinor("1.234", 2), null);
  assert.equal(inputToMinor("-1", 2), null);
  assert.equal(inputToMinor("abc", 2), null);
  assert.equal(inputToMinor("10000001", 2), null); // over the maximum
  assert.equal(minorToInput(1250, 2), "12.50");
  assert.equal(minorToInput(5, 2), "0.05");
  assert.equal(minorToInput(2500, 0), "2500");
});

test("geometry matches the server rules", () => {
  const a = { x: 0, y: 0, width: 10, height: 10 };
  assert.equal(overlaps(a, { x: 10, y: 0, width: 5, height: 5 }), false);
  assert.equal(overlaps(a, { x: 9, y: 9, width: 5, height: 5 }), true);
  assert.equal(fits({ x: 90, y: 50, width: 10, height: 10 }, 100, 60), true);
  assert.equal(fits({ x: 91, y: 0, width: 10, height: 10 }, 100, 60), false);
  assert.deepEqual(clampPosition({ x: 97.6, y: -3, width: 10, height: 10 }, 100, 60), { x: 90, y: 0 });
  assert.deepEqual(freeSpot([a], 10, 100, 60), { x: 10, y: 0 });
  assert.equal(freeSpot([{ x: 0, y: 0, width: 20, height: 20 }], 10, 20, 20), null);
});

test("physicalSize trims only decimal zeros", async () => {
  const { physicalSize } = await import("./types.ts");
  assert.equal(physicalSize({ physical_width: "100.00", physical_depth: "12.50", physical_unit: "FT" }), "100 × 12.5 ft");
  assert.equal(physicalSize({ physical_width: null, physical_depth: "1", physical_unit: "M" }), null);
});
