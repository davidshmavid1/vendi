"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";
import { apiSend, resetCsrf } from "@/lib/django/client";
import type { AccountState } from "@/lib/django/useAccount";
import { loginHref } from "@/lib/applications/logic";

/** Page frame for screens backed by the Django API, with the Django
 *  sign-in status (separate from the legacy organizer login). */
export function DjangoPage({
  account,
  children,
  wide = false,
}: {
  account: AccountState;
  children: ReactNode;
  wide?: boolean;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const [signingOut, setSigningOut] = useState(false);

  async function signOut() {
    setSigningOut(true);
    await apiSend("POST", "/auth/logout");
    resetCsrf();
    router.replace(loginHref(pathname));
  }

  return (
    <main className={`mx-auto w-full ${wide ? "max-w-5xl" : "max-w-3xl"} flex-1 px-4 py-8 sm:px-6`}>
      <nav
        aria-label="Account"
        className="mb-6 flex flex-wrap items-center justify-between gap-3 border-b border-zinc-200 pb-3 text-sm dark:border-zinc-800"
      >
        <div className="flex flex-wrap gap-4">
          <Link href="/markets" className="underline">
            Markets
          </Link>
          <Link href="/vendor/applications" className="underline">
            My applications
          </Link>
          <Link href="/organizer" className="underline">
            Organizer review
          </Link>
        </div>
        {account.status === "signed_in" ? (
          <span className="flex items-center gap-3 text-zinc-600 dark:text-zinc-400">
            <span>{account.account.email}</span>
            <button type="button" onClick={signOut} disabled={signingOut} className="underline disabled:opacity-50">
              Sign out
            </button>
          </span>
        ) : account.status === "anonymous" ? (
          <Link href={loginHref(pathname)} className="underline">
            Sign in
          </Link>
        ) : null}
      </nav>
      {children}
    </main>
  );
}

export function Notice({ tone = "zinc", children }: { tone?: "zinc" | "red" | "amber" | "green"; children: ReactNode }) {
  const tones = {
    zinc: "border-zinc-300 text-zinc-700 dark:border-zinc-700 dark:text-zinc-300",
    red: "border-red-300 text-red-700 dark:border-red-800 dark:text-red-300",
    amber: "border-amber-300 text-amber-800 dark:border-amber-800 dark:text-amber-300",
    green: "border-green-300 text-green-800 dark:border-green-800 dark:text-green-300",
  };
  return (
    <div role={tone === "red" ? "alert" : "status"} className={`rounded-md border p-4 text-sm ${tones[tone]}`}>
      {children}
    </div>
  );
}

/** Shown when a page needs a signed-in Django account. */
export function AccountGate({ account, retry, children }: { account: AccountState; retry: () => void; children: ReactNode }) {
  const pathname = usePathname();
  if (account.status === "loading") return <p className="text-sm text-zinc-500">Loading…</p>;
  if (account.status === "error") {
    return (
      <Notice tone="red">
        {account.error.message}{" "}
        <button type="button" onClick={retry} className="underline">
          Try again
        </button>
      </Notice>
    );
  }
  if (account.status === "anonymous") {
    return (
      <Notice>
        <Link href={loginHref(pathname)} className="font-medium underline">
          Sign in
        </Link>{" "}
        to continue. New to Vendi?{" "}
        <Link href={`/account/register?next=${encodeURIComponent(pathname)}`} className="underline">
          Create an account
        </Link>
        .
      </Notice>
    );
  }
  return <>{children}</>;
}

export const inputClass =
  "w-full min-w-0 rounded-md border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-zinc-500 dark:border-zinc-700 dark:bg-zinc-950";

export const buttonClass =
  "rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-zinc-700 disabled:opacity-50 dark:bg-white dark:text-zinc-900 dark:hover:bg-zinc-200";

export const secondaryButtonClass =
  "rounded-md border border-zinc-300 px-4 py-2 text-sm font-medium hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-800";
