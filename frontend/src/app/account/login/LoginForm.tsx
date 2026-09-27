"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Card, FormError } from "@/components/ui";
import { buttonClass, inputClass } from "@/components/DjangoPage";
import { apiSend, resetCsrf } from "@/lib/django/client";

export function LoginForm({ next }: { next: string }) {
  const router = useRouter();
  const [error, setError] = useState<string>();
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setPending(true);
    setError(undefined);
    const result = await apiSend("POST", "/auth/login", {
      email: String(form.get("email") ?? ""),
      password: String(form.get("password") ?? ""),
    });
    if (result.ok) {
      resetCsrf();
      router.replace(next);
      return;
    }
    setPending(false);
    setError(
      result.status === 429 ? "Too many attempts. Wait a few minutes and try again." : result.error.message,
    );
  }

  return (
    <main className="mx-auto w-full max-w-sm flex-1 px-4 py-12">
      <h1 className="text-2xl font-semibold tracking-tight">Sign in to Vendi</h1>
      <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">For vendors applying to markets and organizers reviewing them.</p>
      <Card className="mt-6">
        <form onSubmit={submit} className="flex flex-col gap-4">
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">Email</span>
            <input name="email" type="email" autoComplete="email" required className={inputClass} />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">Password</span>
            <input name="password" type="password" autoComplete="current-password" required className={inputClass} />
          </label>
          <div role="alert">
            <FormError error={error} />
          </div>
          <button type="submit" disabled={pending} className={buttonClass}>
            {pending ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </Card>
      <p className="mt-4 text-sm">
        New here?{" "}
        <Link href={`/account/register?next=${encodeURIComponent(next)}`} className="underline">
          Create an account
        </Link>
      </p>
    </main>
  );
}
