"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui";
import { AccountGate, DjangoPage, Notice } from "@/components/DjangoPage";
import { apiGet, type ApiError } from "@/lib/django/client";
import { useAccount } from "@/lib/django/useAccount";
import { formatDateTime, STATUS_LABELS } from "@/lib/applications/logic";
import type { MyBusiness, Page, VendorApplication } from "@/lib/applications/types";

type BusinessApplications = { membership: MyBusiness; items: VendorApplication[]; error?: ApiError };

export function VendorApplications() {
  const { state: account, reload } = useAccount();
  const [data, setData] = useState<BusinessApplications[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [attempt, setAttempt] = useState(0);
  const signedIn = account.status === "signed_in";

  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    (async () => {
      const businesses = await apiGet<Page<MyBusiness>>("/vendors?limit=100");
      if (cancelled) return;
      if (!businesses.ok) {
        setError(businesses.error);
        return;
      }
      const rows = await Promise.all(
        businesses.data.items.map(async (membership) => {
          const apps = await apiGet<Page<VendorApplication>>(`/vendors/${membership.business.id}/applications?limit=100`);
          return apps.ok
            ? { membership, items: [...apps.data.items].reverse() }
            : { membership, items: [], error: apps.error };
        }),
      );
      if (!cancelled) {
        setError(null);
        setData(rows);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [signedIn, attempt]);

  return (
    <DjangoPage account={account}>
      <h1 className="text-2xl font-semibold tracking-tight">My applications</h1>
      <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
        Applications from the vendor businesses you belong to. Find dates to apply to under{" "}
        <Link href="/markets" className="underline">
          Markets
        </Link>
        .
      </p>
      <div className="mt-6">
        <AccountGate account={account} retry={reload}>
          {error ? (
            <Notice tone="red">
              {error.message}{" "}
              <button type="button" className="underline" onClick={() => setAttempt((n) => n + 1)}>
                Try again
              </button>
            </Notice>
          ) : data === null ? (
            <p className="text-sm text-zinc-500">Loading…</p>
          ) : data.length === 0 ? (
            <Notice>
              You don&apos;t belong to a vendor business yet.{" "}
              <Link href="/vendor/businesses/new" className="underline">
                Create your business
              </Link>
              .
            </Notice>
          ) : (
            <div className="flex flex-col gap-8">
              {data.map(({ membership, items, error: rowError }) => (
                <section key={membership.business.id} aria-labelledby={`b-${membership.business.id}`}>
                  <h2 id={`b-${membership.business.id}`} className="text-lg font-semibold">
                    {membership.business.name}{" "}
                    <span className="text-sm font-normal text-zinc-500">
                      ({membership.role === "OWNER" ? "owner" : "member"})
                    </span>
                  </h2>
                  {rowError ? (
                    <p className="mt-2 text-sm text-red-600">{rowError.message}</p>
                  ) : items.length === 0 ? (
                    <p className="mt-2 text-sm text-zinc-500">No applications yet.</p>
                  ) : (
                    <ul className="mt-3 flex flex-col gap-2">
                      {items.map((a) => (
                        <li key={a.id}>
                          <Link
                            href={`/vendor/businesses/${membership.business.id}/applications/${a.id}`}
                            className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-zinc-200 bg-white px-4 py-3 text-sm hover:border-zinc-400 dark:border-zinc-800 dark:bg-zinc-900"
                          >
                            <span>
                              <span className="font-medium">{a.occurrence.market_name}</span>
                              <span className="text-zinc-600 dark:text-zinc-400">
                                {" "}
                                · {formatDateTime(a.occurrence.starts_at, a.occurrence.timezone)}
                              </span>
                            </span>
                            <Badge tone={STATUS_LABELS[a.status].tone}>{STATUS_LABELS[a.status].label}</Badge>
                          </Link>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              ))}
            </div>
          )}
        </AccountGate>
      </div>
    </DjangoPage>
  );
}
