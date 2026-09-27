// Pure helpers for stall holds (unit tested with node:test).

/** Difference between the server's clock and this device's, from a
 *  response's server_time and the local time it arrived. */
export function clockOffsetMs(serverTime: string, receivedAtMs: number): number {
  return new Date(serverTime).getTime() - receivedAtMs;
}

/** Whole seconds left on a hold, using server time (never negative). */
export function secondsLeft(expiresAt: string, offsetMs: number, nowMs: number): number {
  return Math.max(0, Math.ceil((new Date(expiresAt).getTime() - (nowMs + offsetMs)) / 1000));
}

export function formatCountdown(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** A fresh idempotency key for one hold attempt (reused only for retries of
 *  that same attempt). */
export function newRequestKey(): string {
  return globalThis.crypto.randomUUID();
}
