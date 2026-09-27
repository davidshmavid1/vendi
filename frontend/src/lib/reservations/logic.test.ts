import { test } from "node:test";
import assert from "node:assert/strict";
import { clockOffsetMs, formatCountdown, newRequestKey, secondsLeft } from "./logic.ts";

test("countdown uses the server clock, not the device's", () => {
  // Device clock is 2 minutes fast: offset is -120s.
  const received = Date.parse("2030-01-01T10:02:00Z");
  const offset = clockOffsetMs("2030-01-01T10:00:00Z", received);
  assert.equal(offset, -120_000);
  assert.equal(secondsLeft("2030-01-01T10:15:00Z", offset, received), 900);
  assert.equal(secondsLeft("2030-01-01T10:15:00Z", offset, received + 899_500), 1);
  assert.equal(secondsLeft("2030-01-01T10:15:00Z", offset, received + 901_000), 0);
});

test("formatCountdown", () => {
  assert.equal(formatCountdown(900), "15:00");
  assert.equal(formatCountdown(61), "1:01");
  assert.equal(formatCountdown(0), "0:00");
});

test("request keys match the server's format", () => {
  const key = newRequestKey();
  assert.match(key, /^[A-Za-z0-9_-]{1,64}$/);
  assert.notEqual(key, newRequestKey());
});
