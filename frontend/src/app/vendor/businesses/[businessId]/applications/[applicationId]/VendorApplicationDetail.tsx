"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApplicationDetails } from "@/components/ApplicationDetails";
import { AccountGate, DjangoPage, Notice, secondaryButtonClass } from "@/components/DjangoPage";
import { apiGet, apiSend, type ApiError } from "@/lib/django/client";
import { useAccount } from "@/lib/django/useAccount";
import type { MyBusiness, VendorApplication } from "@/lib/applications/types";

export function VendorApplicationDetail({ businessId, applicationId }: { businessId: number; applicationId: number }) {
  const { state: account, reload } = useAccount();
  const [application, setApplication] = useState<VendorApplication | null>(null);
  const [role, setRole] = useState<MyBusiness["role"] | null>(null);
  const [error, setError] = useState<{ status: number; error: ApiError } | null>(null);
  const [withdrawing, setWithdrawing] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const signedIn = account.status === "signed_in";

  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    Promise.all([
      apiGet<VendorApplication>(`/vendors/${businessId}/applications/${applicationId}`),
      apiGet<MyBusiness>(`/vendors/${businessId}`),
    ]).then(([app, membership]) => {
      if (cancelled) return;
      if (!app.ok) setError({ status: app.status, error: app.error });
      else setApplication(app.data);
      if (membership.ok) setRole(membership.data.role);
    });
    return () => {
      cancelled = true;
    };
  }, [signedIn, businessId, applicationId]);

  async function withdraw() {
    if (!window.confirm("Withdraw this application? You can't reapply to this date afterwards.")) return;
    setWithdrawing(true);
    setActionError(null);
    const result = await apiSend<VendorApplication>("POST", `/vendors/${businessId}/applications/${applicationId}/withdraw`);
    setWithdrawing(false);
    if (result.ok) setApplication(result.data);
    else setActionError(result.error.message);
  }

  return (
    <DjangoPage account={account}>
      <Link href="/vendor/applications" className="text-sm text-zinc-600 underline dark:text-zinc-400">
        ← My applications
      </Link>
      <h1 className="mt-4 text-2xl font-semibold tracking-tight">Application</h1>
      <div className="mt-6">
        <AccountGate account={account} retry={reload}>
          {error ? (
            <Notice tone="red">{error.status === 404 ? "Application not found." : error.error.message}</Notice>
          ) : !application ? (
            <p className="text-sm text-zinc-500">Loading…</p>
          ) : (
            <div className="flex flex-col gap-6">
              <p className="text-sm text-zinc-600 dark:text-zinc-400">Organized by {application.organizer_name}</p>
              {application.status === "APPROVED" && (
                <Notice tone="green">
                  You&apos;re accepted for this date.{" "}
                  <Link
                    href={`/vendor/businesses/${businessId}/applications/${applicationId}/stall`}
                    className="font-medium underline"
                  >
                    Choose a stall
                  </Link>
                </Notice>
              )}
              <ApplicationDetails application={application} />
              {application.status === "SUBMITTED" && (
                <div className="flex flex-col gap-2">
                  {role === "OWNER" ? (
                    <div>
                      <button type="button" onClick={withdraw} disabled={withdrawing} className={secondaryButtonClass}>
                        {withdrawing ? "Withdrawing…" : "Withdraw application"}
                      </button>
                    </div>
                  ) : (
                    <p className="text-xs text-zinc-500">Only the business owner can withdraw this application.</p>
                  )}
                  {actionError && <Notice tone="red">{actionError}</Notice>}
                </div>
              )}
            </div>
          )}
        </AccountGate>
      </div>
    </DjangoPage>
  );
}
