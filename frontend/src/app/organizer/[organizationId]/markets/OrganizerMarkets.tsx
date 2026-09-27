"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui";
import { AccountGate, DjangoPage, Notice } from "@/components/DjangoPage";
import { apiGet, type ApiError } from "@/lib/django/client";
import { useAccount } from "@/lib/django/useAccount";
import type { Page } from "@/lib/applications/types";

type Market = { id: number; name: string; status: "DRAFT" | "PUBLISHED" | "ARCHIVED"; city: string };

export function OrganizerMarkets({ organizationId }: { organizationId: number }) {
  const { state: account, reload } = useAccount();
  const [markets, setMarkets] = useState<Market[] | null>(null);
  const [error, setError] = useState<{ status: number; error: ApiError } | null>(null);
  const signedIn = account.status === "signed_in";

  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    apiGet<Page<Market>>(`/organizations/${organizationId}/markets?limit=100`).then((r) => {
      if (cancelled) return;
      if (r.ok) setMarkets(r.data.items);
      else setError({ status: r.status, error: r.error });
    });
    return () => {
      cancelled = true;
    };
  }, [signedIn, organizationId]);

  return (
    <DjangoPage account={account}>
      <Link href="/organizer" className="text-sm text-zinc-600 underline dark:text-zinc-400">
        ← Organizations
      </Link>
      <h1 className="mt-4 text-2xl font-semibold tracking-tight">Markets and stall layouts</h1>
      <div className="mt-6">
        <AccountGate account={account} retry={reload}>
          {error ? (
            <Notice tone="red">{error.status === 404 ? "Organization not found." : error.error.message}</Notice>
          ) : markets === null ? (
            <p className="text-sm text-zinc-500">Loading…</p>
          ) : markets.length === 0 ? (
            <Notice>This organization has no markets yet.</Notice>
          ) : (
            <ul className="flex flex-col gap-2">
              {markets.map((m) => (
                <li key={m.id}>
                  <Link
                    href={`/organizer/${organizationId}/markets/${m.id}`}
                    className="flex items-center justify-between rounded-md border border-zinc-200 bg-white px-4 py-3 text-sm hover:border-zinc-400 dark:border-zinc-800 dark:bg-zinc-900"
                  >
                    <span className="font-medium">{m.name}</span>
                    <Badge tone={m.status === "PUBLISHED" ? "green" : "zinc"}>{m.status.toLowerCase()}</Badge>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </AccountGate>
      </div>
    </DjangoPage>
  );
}
