"use client";

import { useCallback, useEffect, useState } from "react";
import { apiGet, type ApiError } from "./client";

export type Account = { id: number; email: string; email_verified: boolean };

export type AccountState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "signed_in"; account: Account }
  | { status: "error"; error: ApiError };

/** The Django session's account (never the legacy Auth.js session). */
export function useAccount() {
  const [state, setState] = useState<AccountState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    apiGet<Account>("/auth/me", controller.signal)
      .then((result) => {
        if (result.ok) setState({ status: "signed_in", account: result.data });
        else if (result.status === 401) setState({ status: "anonymous" });
        else setState({ status: "error", error: result.error });
      })
      .catch(() => {});
    return () => controller.abort();
  }, [attempt]);

  const reload = useCallback(() => {
    setState({ status: "loading" });
    setAttempt((n) => n + 1);
  }, []);

  return { state, reload };
}
