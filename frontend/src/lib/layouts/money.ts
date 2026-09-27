// Money is integer minor units (e.g. cents) plus an ISO 4217 code, as the
// Django API stores it (backend/layouts/money.py). The API returns each
// layout's `currency_exponent`; CURRENCY_EXPONENTS mirrors the server's
// supported list for the editor's currency picker. The server re-validates.

export const CURRENCY_EXPONENTS: Record<string, number> = {
  USD: 2,
  CAD: 2,
  MXN: 2,
  EUR: 2,
  GBP: 2,
  AUD: 2,
  NZD: 2,
  JPY: 0,
  KRW: 0,
};

export const MAX_PRICE_MINOR = 1_000_000_000;

/** Format minor units without assuming two decimal places. */
export function formatMinor(minor: number, currency: string, exponent: number, locale = "en-US"): string {
  const negative = minor < 0;
  const digits = String(Math.abs(Math.trunc(minor))).padStart(exponent + 1, "0");
  const whole = digits.slice(0, digits.length - exponent) || "0";
  const fraction = exponent > 0 ? digits.slice(-exponent) : "";
  // Build the exact decimal string, then let Intl add the symbol and grouping.
  const text = `${negative ? "-" : ""}${whole}${fraction ? `.${fraction}` : ""}`;
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency,
    minimumFractionDigits: exponent,
    maximumFractionDigits: exponent,
  }).format(text as unknown as number);
}

/** Minor units as an editable major-unit string, e.g. 1250 -> "12.50". */
export function minorToInput(minor: number, exponent: number): string {
  if (exponent === 0) return String(minor);
  const digits = String(minor).padStart(exponent + 1, "0");
  return `${digits.slice(0, -exponent)}.${digits.slice(-exponent)}`;
}

/** Parse a major-unit amount typed by a person into minor units, exactly
 *  (string arithmetic, no floats). Returns null when invalid. */
export function inputToMinor(text: string, exponent: number): number | null {
  const value = text.trim().replace(/,/g, "");
  const pattern = exponent === 0 ? /^(\d{1,12})$/ : new RegExp(`^(\\d{1,12})(?:\\.(\\d{1,${exponent}}))?$`);
  const match = pattern.exec(value);
  if (!match) return null;
  const fraction = (match[2] ?? "").padEnd(exponent, "0");
  const minor = Number(match[1]) * 10 ** exponent + Number(fraction || "0");
  return Number.isSafeInteger(minor) && minor <= MAX_PRICE_MINOR ? minor : null;
}
