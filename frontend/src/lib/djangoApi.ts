import "server-only";

// Server-side reads from the Django API's public endpoints. No cookies or
// credentials are forwarded: these endpoints are anonymous.

export function djangoOrigin(): string | null {
  const origin = process.env.DJANGO_API_ORIGIN?.replace(/\/+$/, "");
  return origin || null;
}

export type PublicResult<T> = { ok: true; data: T } | { ok: false; status: number };

export async function getPublic<T>(path: string): Promise<PublicResult<T>> {
  const origin = djangoOrigin();
  if (!origin) return { ok: false, status: 503 };
  try {
    const response = await fetch(`${origin}/api/v1/public${path}`, {
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) return { ok: false, status: response.status };
    return { ok: true, data: (await response.json()) as T };
  } catch {
    return { ok: false, status: 502 };
  }
}
