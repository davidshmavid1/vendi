"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui";
import { AccountGate, DjangoPage, Notice, secondaryButtonClass } from "@/components/DjangoPage";
import { apiGet, apiSend, type ApiError } from "@/lib/django/client";
import { useAccount } from "@/lib/django/useAccount";
import { formatDateTime } from "@/lib/applications/logic";
import type { MyOrganization } from "@/lib/applications/types";
import type { VersionOut, VersionSummary } from "@/lib/layouts/types";
import { CancellationPolicySection } from "./CancellationPolicySection";

type Occurrence = { id: number; starts_at: string; ends_at: string; timezone: string; status: "SCHEDULED" | "CANCELLED" };
type Market = { id: number; name: string; vendor_cancellation_cutoff_hours: number | null };

export function MarketDates({ organizationId, marketId }: { organizationId: number; marketId: number }) {
  const router = useRouter();
  const { state: account, reload } = useAccount();
  const [market, setMarket] = useState<Market | null>(null);
  const [versions, setVersions] = useState<VersionSummary[]>([]);
  const [role, setRole] = useState<MyOrganization["role"] | null>(null);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [dates, setDates] = useState<Occurrence[] | null>(null);
  const [error, setError] = useState<{ status: number; error: ApiError } | null>(null);
  const signedIn = account.status === "signed_in";

  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    const base = `/organizations/${organizationId}/markets/${marketId}`;
    Promise.all([
      apiGet<Market>(base),
      apiGet<{ items: Occurrence[] }>(`${base}/occurrences?limit=100`),
      apiGet<{ items: VersionSummary[] }>(`${base}/layout-versions`),
      apiGet<MyOrganization>(`/organizations/${organizationId}`),
    ]).then(([m, d, v, membership]) => {
      if (cancelled) return;
      if (!m.ok) return setError({ status: m.status, error: m.error });
      if (!d.ok) return setError({ status: d.status, error: d.error });
      setMarket(m.data);
      if (v.ok) setVersions(v.data.items);
      if (membership.ok) setRole(membership.data.role);
      const now = Date.now();
      setDates(d.data.items.filter((o) => new Date(o.ends_at).getTime() > now));
    });
    return () => {
      cancelled = true;
    };
  }, [signedIn, organizationId, marketId]);

  async function newLayout() {
    setCreating(true);
    setCreateError(null);
    const result = await apiSend<VersionOut>("POST", `/organizations/${organizationId}/markets/${marketId}/layout-versions`, {});
    setCreating(false);
    if (result.ok) router.push(`/organizer/${organizationId}/markets/${marketId}/layouts/${result.data.id}`);
    else setCreateError(result.error.message);
  }

  const canEdit = role === "OWNER" || role === "ADMIN";

  return (
    <DjangoPage account={account}>
      <Link href={`/organizer/${organizationId}/markets`} className="text-sm text-zinc-600 underline dark:text-zinc-400">
        ← Markets
      </Link>
      <h1 className="mt-4 text-2xl font-semibold tracking-tight">{market?.name ?? "Market"}</h1>
      <div className="mt-6">
        <AccountGate account={account} retry={reload}>
          {error ? (
            <Notice tone="red">{error.status === 404 ? "Market not found." : error.error.message}</Notice>
          ) : dates === null ? (
            <p className="text-sm text-zinc-500">Loading…</p>
          ) : (
            <div className="flex flex-col gap-8">
              <section aria-labelledby="layouts-heading">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h2 id="layouts-heading" className="text-lg font-semibold">
                    Stall layouts
                  </h2>
                  {canEdit && (
                    <button type="button" onClick={newLayout} disabled={creating} className={secondaryButtonClass}>
                      {creating ? "Creating…" : "New layout"}
                    </button>
                  )}
                </div>
                <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
                  A layout is the physical plan of stalls. Dates choose a layout and set their own prices. A layout used by a
                  date can&apos;t change; copy it to plan changes.
                </p>
                {createError && <p className="mt-2 text-sm text-red-600">{createError}</p>}
                {versions.length === 0 ? (
                  <p className="mt-3 text-sm text-zinc-500">No layouts yet.</p>
                ) : (
                  <ul className="mt-3 flex flex-col gap-2">
                    {versions.map((v) => (
                      <li key={v.id}>
                        <Link
                          href={`/organizer/${organizationId}/markets/${marketId}/layouts/${v.id}`}
                          className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-zinc-200 bg-white px-4 py-3 text-sm hover:border-zinc-400 dark:border-zinc-800 dark:bg-zinc-900"
                        >
                          <span className="font-medium">Layout v{v.number}</span>
                          <span className="flex items-center gap-2 text-zinc-600 dark:text-zinc-400">
                            {v.stall_count} stalls · used by {v.date_count} date{v.date_count === 1 ? "" : "s"}
                            <Badge tone={v.locked ? "blue" : "zinc"}>{v.locked ? "in use" : "draft"}</Badge>
                          </span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              {market && (
                <CancellationPolicySection
                  organizationId={organizationId}
                  marketId={marketId}
                  initialHours={market.vendor_cancellation_cutoff_hours}
                  canEdit={canEdit}
                />
              )}

              <section aria-labelledby="dates-heading">
                <h2 id="dates-heading" className="text-lg font-semibold">
                  Upcoming dates
                </h2>
                {dates.length === 0 ? (
                  <p className="mt-3 text-sm text-zinc-500">No upcoming dates.</p>
                ) : (
                  <ul className="mt-3 flex flex-col gap-2">
                    {dates.map((o) => (
                      <li
                        key={o.id}
                        className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-zinc-200 bg-white px-4 py-3 text-sm dark:border-zinc-800 dark:bg-zinc-900"
                      >
                        <span>
                          {formatDateTime(o.starts_at, o.timezone)} {o.status === "CANCELLED" && <Badge tone="red">Cancelled</Badge>}
                        </span>
                        <span className="flex gap-4">
                          <Link href={`/organizer/${organizationId}/markets/${marketId}/dates/${o.id}/layout`} className="underline">
                            Stalls and prices
                          </Link>
                          <Link href={`/organizer/${organizationId}/markets/${marketId}/dates/${o.id}/bookings`} className="underline">
                            Bookings
                          </Link>
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </div>
          )}
        </AccountGate>
      </div>
    </DjangoPage>
  );
}
