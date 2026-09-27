"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { Card, FormError } from "@/components/ui";
import { buttonClass, inputClass, Notice } from "@/components/DjangoPage";
import { apiSend } from "@/lib/django/client";
import { loginHref } from "@/lib/applications/logic";

export function RegisterForm({ next }: { next: string }) {
  const [error, setError] = useState<string>();
  const [pending, setPending] = useState(false);
  const [sentTo, setSentTo] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const email = String(form.get("email") ?? "");
    setPending(true);
    setError(undefined);
    const result = await apiSend("POST", "/auth/register", { email, password: String(form.get("password") ?? "") });
    setPending(false);
    if (result.ok) setSentTo(email);
    else setError(result.status === 429 ? "Too many attempts. Try again later." : result.error.message);
  }

  return (
    <main className="mx-auto w-full max-w-sm flex-1 px-4 py-12">
      <h1 className="text-2xl font-semibold tracking-tight">Create a Vendi account</h1>
      {sentTo ? (
        <div className="mt-6">
          <Notice tone="green">
            We sent a confirmation link to {sentTo}. Open it to confirm your email, then{" "}
            <Link href={loginHref(next)} className="underline">
              sign in
            </Link>
            .
          </Notice>
        </div>
      ) : (
        <Card className="mt-6">
          <form onSubmit={submit} className="flex flex-col gap-4">
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium">Email</span>
              <input name="email" type="email" autoComplete="email" required className={inputClass} />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium">Password</span>
              <input name="password" type="password" autoComplete="new-password" required minLength={8} className={inputClass} />
              <span className="text-xs text-zinc-500">At least 8 characters, not a common password.</span>
            </label>
            <div role="alert">
              <FormError error={error} />
            </div>
            <button type="submit" disabled={pending} className={buttonClass}>
              {pending ? "Creating…" : "Create account"}
            </button>
          </form>
        </Card>
      )}
      <p className="mt-4 text-sm">
        Already have an account?{" "}
        <Link href={loginHref(next)} className="underline">
          Sign in
        </Link>
      </p>
    </main>
  );
}
