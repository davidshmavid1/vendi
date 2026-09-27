"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Card } from "@/components/ui";
import { ApplicationDetails } from "@/components/ApplicationDetails";
import { AccountGate, buttonClass, DjangoPage, inputClass, Notice, secondaryButtonClass } from "@/components/DjangoPage";
import { apiGet, apiSend, type ApiError } from "@/lib/django/client";
import { useAccount } from "@/lib/django/useAccount";
import { formatDateTime } from "@/lib/applications/logic";
import type { MyOrganization, OrganizerApplication } from "@/lib/applications/types";

export function ReviewApplication({ organizationId, applicationId }: { organizationId: number; applicationId: number }) {
  const { state: account, reload } = useAccount();
  const [application, setApplication] = useState<OrganizerApplication | null>(null);
  const [role, setRole] = useState<MyOrganization["role"] | null>(null);
  const [error, setError] = useState<{ status: number; error: ApiError } | null>(null);
  const [message, setMessage] = useState("");
  const [deciding, setDeciding] = useState<"approve" | "reject" | null>(null);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [attempt, setAttempt] = useState(0);
  const signedIn = account.status === "signed_in";
  const base = `/organizations/${organizationId}/applications/${applicationId}`;

  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    Promise.all([apiGet<OrganizerApplication>(base), apiGet<MyOrganization>(`/organizations/${organizationId}`)]).then(
      ([app, membership]) => {
        if (cancelled) return;
        if (!app.ok) setError({ status: app.status, error: app.error });
        else {
          setError(null);
          setApplication(app.data);
        }
        if (membership.ok) setRole(membership.data.role);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [signedIn, base, organizationId, attempt]);

  async function decide(action: "approve" | "reject") {
    const prompt =
      action === "approve"
        ? "Approve this application? The vendor will see your message. This doesn't reserve a stall or take payment."
        : "Decline this application? The vendor will see your message. This can't be undone.";
    if (!window.confirm(prompt)) return;
    setDeciding(action);
    setActionError(null);
    const result = await apiSend<OrganizerApplication>("POST", `${base}/${action}`, { message });
    setDeciding(null);
    if (result.ok) {
      setApplication(result.data);
      setMessage("");
    } else {
      setActionError(result.error);
      if (result.error.code === "application_not_submitted") setAttempt((n) => n + 1);
    }
  }

  const canDecide = role === "OWNER" || role === "ADMIN";

  return (
    <DjangoPage account={account}>
      <Link href={`/organizer/${organizationId}/applications`} className="text-sm text-zinc-600 underline dark:text-zinc-400">
        ← Applications
      </Link>
      <h1 className="mt-4 text-2xl font-semibold tracking-tight">
        {application ? application.vendor_snapshot.name : "Application"}
      </h1>
      <div className="mt-6">
        <AccountGate account={account} retry={reload}>
          {error ? (
            <Notice tone="red">{error.status === 404 ? "Application not found." : error.error.message}</Notice>
          ) : !application ? (
            <p className="text-sm text-zinc-500">Loading…</p>
          ) : (
            <div className="flex flex-col gap-6">
              {application.status === "SUBMITTED" &&
                (canDecide ? (
                  <Card>
                    <h2 className="text-sm font-semibold">Decision</h2>
                    <label className="mt-3 flex flex-col gap-1 text-sm">
                      <span>Message to the vendor (optional, visible to them)</span>
                      <textarea
                        value={message}
                        onChange={(e) => setMessage(e.target.value)}
                        maxLength={2000}
                        rows={3}
                        className={inputClass}
                      />
                    </label>
                    {actionError && (
                      <div className="mt-3">
                        <Notice tone="red">{actionError.message}</Notice>
                      </div>
                    )}
                    <div className="mt-4 flex flex-wrap gap-3">
                      <button type="button" onClick={() => decide("approve")} disabled={deciding !== null} className={buttonClass}>
                        {deciding === "approve" ? "Approving…" : "Approve"}
                      </button>
                      <button
                        type="button"
                        onClick={() => decide("reject")}
                        disabled={deciding !== null}
                        className={secondaryButtonClass}
                      >
                        {deciding === "reject" ? "Declining…" : "Decline"}
                      </button>
                    </div>
                    <p className="mt-3 text-xs text-zinc-500">
                      Approving accepts the vendor for this date. It doesn&apos;t assign or reserve a stall, and no payment is
                      taken.
                    </p>
                  </Card>
                ) : (
                  <Notice>You can view this application. Owners and admins decide applications.</Notice>
                ))}
              {application.status !== "SUBMITTED" && actionError && <Notice tone="amber">{actionError.message}</Notice>}
              <ApplicationDetails application={application} />
              <Card>
                <h2 className="text-sm font-semibold">History</h2>
                <ol className="mt-2 flex flex-col gap-1 text-sm">
                  {application.history.map((h, i) => (
                    <li key={i}>
                      {formatDateTime(h.at, application.occurrence.timezone)}: {h.from_status ? `${h.from_status} → ` : ""}
                      {h.to_status} (user #{h.actor_user_id})
                    </li>
                  ))}
                </ol>
              </Card>
            </div>
          )}
        </AccountGate>
      </div>
    </DjangoPage>
  );
}
