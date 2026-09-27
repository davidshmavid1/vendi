"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui";
import { AccountGate, DjangoPage, Notice } from "@/components/DjangoPage";
import { apiGet, type ApiError } from "@/lib/django/client";
import { useAccount } from "@/lib/django/useAccount";
import { formatDateTime } from "@/lib/applications/logic";

type Occurrence = { id: number; starts_at: string; ends_at: string; timezone: string; status: "SCHEDULED" | "CANCELLED" };
type Market = { id: number; name: string };

export function MarketDates({ organizationId, marketId }: { organizationId: number; marketId: number }) {
  const { state: account, reload } = useAccount();
  const [market, setMarket] = useState<Market | null>(null);
  const [dates, setDates] = useState<Occurrence[] | null>(null);
  const [error, setError] = useState<{ status: number; error: ApiError } | null>(null);
  const signedIn = account.status === "signed_in";

  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    const base = `/organizations/${organizationId}/markets/${marketId}`;
    Promise.all([apiGet<Market>(base), apiGet<{ items: Occurrence[] }>(`${base}/occurrences?limit=100`)]).then(([m, d]) => {
      if (cancelled) return;
      if (!m.ok) return setError({ status: m.status, error: m.error });
      if (!d.ok) return setError({ status: d.status, error: d.error });
      setMarket(m.data);
      const now = Date.now();
      setDates(d.data.items.filter((o) => new Date(o.ends_at).getTime() > now));
    });
    return () => {
      cancelled = true;
    };
  }, [signedIn, organizationId, marketId]);

  return (
    <DjangoPage account={account}>
      <Link href={`/organizer/${organizationId}/markets`} className="text-sm text-zinc-600 underline dark:text-zinc-400">
        ← Markets
      </Link>
      <h1 className="mt-4 text-2xl font-semibold tracking-tight">{market?.name ?? "Market"}: upcoming dates</h1>
      <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">Each date has its own stall layout and prices.</p>
      <div className="mt-6">
        <AccountGate account={account} retry={reload}>
          {error ? (
            <Notice tone="red">{error.status === 404 ? "Market not found." : error.error.message}</Notice>
          ) : dates === null ? (
            <p className="text-sm text-zinc-500">Loading…</p>
          ) : dates.length === 0 ? (
            <Notice>No upcoming dates.</Notice>
          ) : (
            <ul className="flex flex-col gap-2">
              {dates.map((o) => (
                <li
                  key={o.id}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-zinc-200 bg-white px-4 py-3 text-sm dark:border-zinc-800 dark:bg-zinc-900"
                >
                  <span>
                    {formatDateTime(o.starts_at, o.timezone)} {o.status === "CANCELLED" && <Badge tone="red">Cancelled</Badge>}
                  </span>
                  <Link href={`/organizer/${organizationId}/markets/${marketId}/dates/${o.id}/layout`} className="underline">
                    Stall layout
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
