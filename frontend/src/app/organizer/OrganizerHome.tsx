"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AccountGate, DjangoPage, Notice } from "@/components/DjangoPage";
import { apiGet, type ApiError } from "@/lib/django/client";
import { useAccount } from "@/lib/django/useAccount";
import type { MyOrganization, Page } from "@/lib/applications/types";

export function OrganizerHome() {
  const { state: account, reload } = useAccount();
  const [orgs, setOrgs] = useState<MyOrganization[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const signedIn = account.status === "signed_in";

  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    apiGet<Page<MyOrganization>>("/organizations?limit=100").then((result) => {
      if (cancelled) return;
      if (result.ok) setOrgs(result.data.items);
      else setError(result.error);
    });
    return () => {
      cancelled = true;
    };
  }, [signedIn]);

  return (
    <DjangoPage account={account}>
      <h1 className="text-2xl font-semibold tracking-tight">Organizer review</h1>
      <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">Review vendor applications and set up stall layouts for your organizations&apos; market dates.</p>
      <div className="mt-6">
        <AccountGate account={account} retry={reload}>
          {error ? (
            <Notice tone="red">{error.message}</Notice>
          ) : orgs === null ? (
            <p className="text-sm text-zinc-500">Loading…</p>
          ) : orgs.length === 0 ? (
            <Notice>You aren&apos;t a member of any organization.</Notice>
          ) : (
            <ul className="flex flex-col gap-2">
              {orgs.map((m) => (
                <li
                  key={m.organization.id}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-zinc-200 bg-white px-4 py-3 text-sm dark:border-zinc-800 dark:bg-zinc-900"
                >
                  <span>
                    <span className="font-medium">{m.organization.name}</span>{" "}
                    <span className="text-zinc-500">({m.role.toLowerCase()})</span>
                  </span>
                  <span className="flex gap-4">
                    <Link href={`/organizer/${m.organization.id}/applications`} className="underline">
                      Applications
                    </Link>
                    <Link href={`/organizer/${m.organization.id}/markets`} className="underline">
                      Stall layouts
                    </Link>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </AccountGate>
      </div>
    </DjangoPage>
  );
}
