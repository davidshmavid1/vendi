"use client";

import Link from "next/link";
import { useState } from "react";
import { buttonClass, Notice } from "@/components/DjangoPage";
import { apiSend } from "@/lib/django/client";

// Confirming is an explicit click (not automatic on load), so link
// scanners that prefetch the URL don't consume the token.
export function VerifyEmail({ token }: { token: string }) {
  const [state, setState] = useState<"idle" | "pending" | "done" | "failed">("idle");
  const [message, setMessage] = useState("");

  async function confirm() {
    setState("pending");
    const result = await apiSend("POST", "/auth/verify-email", { token });
    if (result.ok) setState("done");
    else {
      setState("failed");
      setMessage(result.error.message);
    }
  }

  return (
    <main className="mx-auto w-full max-w-sm flex-1 px-4 py-12">
      <h1 className="text-2xl font-semibold tracking-tight">Confirm your email</h1>
      <div className="mt-6 flex flex-col gap-4">
        {!token ? (
          <Notice tone="red">This link is incomplete. Open the link from your email again.</Notice>
        ) : state === "done" ? (
          <Notice tone="green">
            Your email is confirmed.{" "}
            <Link href="/account/login" className="underline">
              Sign in
            </Link>
          </Notice>
        ) : (
          <>
            {state === "failed" && <Notice tone="red">{message}</Notice>}
            <button type="button" onClick={confirm} disabled={state === "pending"} className={buttonClass}>
              {state === "pending" ? "Confirming…" : "Confirm my email"}
            </button>
          </>
        )}
      </div>
    </main>
  );
}
