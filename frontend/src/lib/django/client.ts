// Browser calls to the Django API through the same-origin /api/v1 forwarding
// rule (next.config.ts). Django's session cookie is HttpOnly and set on this
// origin; the CSRF token is fetched from /auth/csrf and kept in memory only
// (never localStorage), as described in backend/ARCHITECTURE.md.

export type ApiError = {
  code: string;
  message: string;
  details?: Record<string, unknown>[] | null;
};

export type ApiResult<T> =
  | { ok: true; status: number; data: T }
  | { ok: false; status: number; error: ApiError };

let csrf: Promise<string | null> | null = null;

async function csrfToken(): Promise<string | null> {
  csrf ??= fetch("/api/v1/auth/csrf", { credentials: "same-origin", cache: "no-store" })
    .then((r) => (r.ok ? r.json() : null))
    .then((body) => (body?.csrf_token as string | undefined) ?? null)
    .catch(() => null);
  const token = await csrf;
  if (token === null) csrf = null;
  return token;
}

/** Login and logout rotate the token; call this after either. */
export function resetCsrf() {
  csrf = null;
}

const UNAVAILABLE: ApiError = {
  code: "unavailable",
  message: "We couldn't reach Vendi right now. Check your connection and try again.",
};

async function parse<T>(response: Response): Promise<ApiResult<T>> {
  if (response.status === 204) return { ok: true, status: 204, data: undefined as T };
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // Not JSON: e.g. the forwarding rule isn't configured, or a proxy error.
  }
  if (response.ok && body !== null) return { ok: true, status: response.status, data: body as T };
  const error = (body as { error?: ApiError } | null)?.error;
  if (error?.code) return { ok: false, status: response.status, error };
  if (response.status === 422) {
    return {
      ok: false,
      status: 422,
      error: { code: "validation_error", message: "Some fields are invalid. Check the form and try again." },
    };
  }
  return { ok: false, status: response.status, error: UNAVAILABLE };
}

export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<ApiResult<T>> {
  try {
    const response = await fetch(`/api/v1${path}`, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal,
    });
    return parse<T>(response);
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    return { ok: false, status: 0, error: UNAVAILABLE };
  }
}

export async function apiSend<T>(
  method: "POST" | "PUT" | "PATCH",
  path: string,
  body?: unknown,
): Promise<ApiResult<T>> {
  const send = async () => {
    const token = await csrfToken();
    const response = await fetch(`/api/v1${path}`, {
      method,
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        ...(token ? { "X-CSRFToken": token } : {}),
      },
      body: JSON.stringify(body ?? {}),
    });
    return parse<T>(response);
  };
  try {
    let result = await send();
    if (!result.ok && result.error.code === "csrf_failed") {
      // The token rotated (e.g. login in another tab): fetch a new one once.
      resetCsrf();
      result = await send();
    }
    return result;
  } catch {
    return { ok: false, status: 0, error: UNAVAILABLE };
  }
}
