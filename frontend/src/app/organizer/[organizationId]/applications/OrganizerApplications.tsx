"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui";
import { AccountGate, DjangoPage, inputClass, Notice, secondaryButtonClass } from "@/components/DjangoPage";
import { apiGet, type ApiError } from "@/lib/django/client";
import { useAccount } from "@/lib/django/useAccount";
import { formatDateTime, STATUS_LABELS, type ApplicationStatus } from "@/lib/applications/logic";
import type { MyOrganization, OrganizerApplicationSummary, Page } from "@/lib/applications/types";

const FILTERS: { value: ApplicationStatus | ""; label: string }[] = [
  { value: "SUBMITTED", label: "Awaiting review" },
  { value: "APPROVED", label: "Approved" },
  { value: "REJECTED", label: "Not accepted" },
  { value: "WITHDRAWN", label: "Withdrawn" },
  { value: "", label: "All" },
];

export function OrganizerApplications({ organizationId }: { organizationId: number }) {
  const { state: account, reload } = useAccount();
  const [org, setOrg] = useState<MyOrganization | null>(null);
  const [status, setStatus] = useState<ApplicationStatus | "">("SUBMITTED");
  const [items, setItems] = useState<OrganizerApplicationSummary[] | null>(null);
  const [cursor, setCursor] = useState<number | null>(null);
  const [error, setError] = useState<{ status: number; error: ApiError } | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const signedIn = account.status === "signed_in";
  const query = `/organizations/${organizationId}/applications?limit=25${status ? `&status=${status}` : ""}`;

  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    Promise.all([apiGet<MyOrganization>(`/organizations/${organizationId}`), apiGet<Page<OrganizerApplicationSummary>>(query)]).then(
      ([membership, page]) => {
        if (cancelled) return;
        if (membership.ok) setOrg(membership.data);
        if (!page.ok) {
          setError({ status: page.status, error: page.error });
          return;
        }
        setError(null);
        setItems(page.data.items);
        setCursor(page.data.next_cursor);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [signedIn, organizationId, query]);

  async function loadMore() {
    if (cursor === null) return;
    setLoadingMore(true);
    const page = await apiGet<Page<OrganizerApplicationSummary>>(`${query}&cursor=${cursor}`);
    setLoadingMore(false);
    if (page.ok) {
      setItems((current) => [...(current ?? []), ...page.data.items]);
      setCursor(page.data.next_cursor);
    }
  }

  return (
    <DjangoPage account={account} wide>
      <Link href="/organizer" className="text-sm text-zinc-600 underline dark:text-zinc-400">
        ← Organizations
      </Link>
      <h1 className="mt-4 text-2xl font-semibold tracking-tight">Applications{org ? ` · ${org.organization.name}` : ""}</h1>
      {org?.role === "STAFF" && <p className="mt-1 text-sm text-zinc-500">You can view applications; owners and admins decide them.</p>}
      <div className="mt-6">
        <AccountGate account={account} retry={reload}>
          {error ? (
            <Notice tone="red">{error.status === 404 ? "Organization not found." : error.error.message}</Notice>
          ) : (
            <>
              <label className="flex max-w-xs flex-col gap-1 text-sm">
                <span className="font-medium">Show</span>
                <select
                  value={status}
                  onChange={(e) => {
                    setItems(null);
                    setStatus(e.target.value as ApplicationStatus | "");
                  }}
                  className={inputClass}
                >
                  {FILTERS.map((f) => (
                    <option key={f.value} value={f.value}>
                      {f.label}
                    </option>
                  ))}
                </select>
              </label>
              {items === null ? (
                <p className="mt-4 text-sm text-zinc-500">Loading…</p>
              ) : items.length === 0 ? (
                <p className="mt-4 text-sm text-zinc-500">No applications here.</p>
              ) : (
                <div className="mt-4 overflow-x-auto">
                  <table className="w-full min-w-[36rem] text-left text-sm">
                    <thead className="text-xs text-zinc-500">
                      <tr>
                        <th className="py-2 pr-4 font-medium">Vendor</th>
                        <th className="py-2 pr-4 font-medium">Market date</th>
                        <th className="py-2 pr-4 font-medium">Submitted</th>
                        <th className="py-2 font-medium">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((a) => (
                        <tr key={a.id} className="border-t border-zinc-200 dark:border-zinc-800">
                          <td className="py-2 pr-4">
                            <Link href={`/organizer/${organizationId}/applications/${a.id}`} className="font-medium underline">
                              {a.vendor_name}
                            </Link>
                          </td>
                          <td className="py-2 pr-4">
                            {a.occurrence.market_name} · {formatDateTime(a.occurrence.starts_at, a.occurrence.timezone)}
                          </td>
                          <td className="py-2 pr-4">{formatDateTime(a.submitted_at, a.occurrence.timezone)}</td>
                          <td className="py-2">
                            <Badge tone={STATUS_LABELS[a.status].tone}>{STATUS_LABELS[a.status].label}</Badge>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {cursor !== null && (
                <button type="button" onClick={loadMore} disabled={loadingMore} className={`mt-4 ${secondaryButtonClass}`}>
                  {loadingMore ? "Loading…" : "Load more"}
                </button>
              )}
            </>
          )}
        </AccountGate>
      </div>
    </DjangoPage>
  );
}
