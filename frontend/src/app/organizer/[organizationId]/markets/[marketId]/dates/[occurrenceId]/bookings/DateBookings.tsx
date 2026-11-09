"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui";
import { AccountGate, buttonClass, DjangoPage, inputClass, Notice, secondaryButtonClass } from "@/components/DjangoPage";
import { CancelBookingPanel } from "@/components/payments/CancelBookingPanel";
import { apiGet, apiSend, type ApiError } from "@/lib/django/client";
import { useAccount } from "@/lib/django/useAccount";
import { formatDateTime } from "@/lib/applications/logic";
import type { MyOrganization } from "@/lib/applications/types";
import { formatMinor } from "@/lib/layouts/money";
import type { Booking, BookingPage, DateCancellation, RefundStatus } from "@/lib/payments/types";

type Props = { organizationId: number; marketId: number; occurrenceId: number };
type Occurrence = { id: number; starts_at: string; timezone: string; status: "SCHEDULED" | "CANCELLED" };

const REFUND_LABEL: Record<RefundStatus, string> = {
  REQUESTED: "Refund in progress",
  PENDING: "Refund in progress",
  SUCCEEDED: "Refunded",
  FAILED: "Refund failed (support)",
  CANCELED: "Refund failed (support)",
  REVIEW: "Refund under review",
};

/** A date's bookings (any team member), with cancellation for owners and
 *  admins: per booking, or the whole date. */
export function DateBookings({ organizationId, marketId, occurrenceId }: Props) {
  const { state: account, reload } = useAccount();
  const signedIn = account.status === "signed_in";
  const marketBase = `/organizations/${organizationId}/markets/${marketId}`;
  const progressUrl = `${marketBase}/occurrences/${occurrenceId}/cancellation`;
  const [bookings, setBookings] = useState<Booking[] | null>(null);
  const [occurrence, setOccurrence] = useState<Occurrence | null>(null);
  const [role, setRole] = useState<MyOrganization["role"] | null>(null);
  const [progress, setProgress] = useState<DateCancellation | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [dateMessage, setDateMessage] = useState("");
  const [confirmDate, setConfirmDate] = useState(false);
  const [notice, setNotice] = useState<{ tone: "red" | "green" | "amber"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const items: Booking[] = [];
    let cursor: number | null = null;
    do {
      const query: string = `occurrence_id=${occurrenceId}${cursor ? `&cursor=${cursor}` : ""}`;
      const result = await apiGet<BookingPage>(`/organizations/${organizationId}/bookings?${query}`);
      if (!result.ok) return setError(result.error);
      items.push(...result.data.items);
      cursor = result.data.next_cursor;
    } while (cursor);
    const [dates, membership, cancellation] = await Promise.all([
      apiGet<{ items: Occurrence[] }>(`${marketBase}/occurrences?limit=100`),
      apiGet<MyOrganization>(`/organizations/${organizationId}`),
      apiGet<DateCancellation>(progressUrl),
    ]);
    setBookings(items);
    if (dates.ok) setOccurrence(dates.data.items.find((o) => o.id === occurrenceId) ?? null);
    if (membership.ok) setRole(membership.data.role);
    if (cancellation.ok) setProgress(cancellation.data);
  }, [organizationId, occurrenceId, marketBase, progressUrl]);

  useEffect(() => {
    if (!signedIn) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch
    void load();
  }, [signedIn, load]);

  const canManage = role === "OWNER" || role === "ADMIN";

  async function cancelDate() {
    setBusy(true);
    setNotice(null);
    const result = await apiSend("POST", `${marketBase}/occurrences/${occurrenceId}/cancel`, { message: dateMessage });
    setBusy(false);
    setConfirmDate(false);
    if (result.ok) {
      setNotice({ tone: "green", text: "Date cancelled. Bookings are being cancelled and refunded." });
    } else {
      setNotice({ tone: "red", text: result.status === 0 ? "We couldn't reach Vendi." : result.error.message });
    }
    void load();
  }

  async function retry() {
    setBusy(true);
    const result = await apiSend<DateCancellation>("POST", `${progressUrl}/process`);
    setBusy(false);
    if (result.ok) setProgress(result.data);
    else setNotice({ tone: "red", text: result.status === 0 ? "We couldn't reach Vendi." : result.error.message });
    void load();
  }

  const zone = occurrence?.timezone ?? bookings?.[0]?.occurrence.timezone ?? "UTC";
  return (
    <DjangoPage account={account}>
      <Link href={`/organizer/${organizationId}/markets/${marketId}`} className="text-sm text-zinc-600 underline dark:text-zinc-400">
        ← Market
      </Link>
      <h1 className="mt-4 text-2xl font-semibold tracking-tight">Bookings for this date</h1>
      {occurrence && (
        <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
          {formatDateTime(occurrence.starts_at, zone)}{" "}
          {occurrence.status === "CANCELLED" && <Badge tone="red">Date cancelled</Badge>}
        </p>
      )}
      <div className="mt-6 flex flex-col gap-6">
        <AccountGate account={account} retry={reload}>
          {error ? (
            <Notice tone="red">{error.message}</Notice>
          ) : !bookings ? (
            <p className="text-sm text-zinc-500">Loading…</p>
          ) : (
            <>
              {notice && <Notice tone={notice.tone}>{notice.text}</Notice>}
              {progress?.cancelled && (
                <Notice tone={progress.pending ? "amber" : "green"}>
                  {progress.pending
                    ? "This date is cancelled. Some cancellations or refunds are still being processed or need Vendi support; this is retried automatically."
                    : "This date is cancelled and all its bookings, holds and refunds have been handled."}
                  {progress.pending && canManage && (
                    <button type="button" onClick={retry} disabled={busy} className={`${secondaryButtonClass} ml-3`}>
                      Retry now
                    </button>
                  )}
                </Notice>
              )}

              {bookings.length === 0 ? (
                <Notice>No stalls are booked for this date.</Notice>
              ) : (
                <ul className="flex flex-col gap-3">
                  {bookings.map((b) => (
                    <li key={b.id} className="rounded-md border border-zinc-200 p-3 text-sm dark:border-zinc-800">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span>
                          <span className="font-medium">{b.stall.label}</span> · {b.vendor_business.name} ·{" "}
                          {formatMinor(b.price_minor, b.currency, b.currency_exponent)}{" "}
                          {b.payment_required ? <Badge tone="green">Paid</Badge> : <Badge>Free</Badge>}
                        </span>
                        <span className="flex flex-wrap gap-2">
                          <Badge tone={b.status === "CANCELLED" ? "red" : "blue"}>
                            {b.status === "CANCELLED" ? "Cancelled" : "Confirmed"}
                          </Badge>
                          {b.refund && (
                            <Badge tone={b.refund.status === "SUCCEEDED" ? "green" : "yellow"}>
                              {REFUND_LABEL[b.refund.status]}
                            </Badge>
                          )}
                        </span>
                      </div>
                      {b.cancellation && (
                        <p className="mt-2 text-zinc-600 dark:text-zinc-400">
                          {b.cancellation.reason && <>Reason: {b.cancellation.reason}. </>}
                          {b.cancellation.internal_note && <>Internal note: {b.cancellation.internal_note}</>}
                        </p>
                      )}
                      {b.status === "CONFIRMED" && canManage && occurrence?.status !== "CANCELLED" && (
                        <div className="mt-3">
                          <CancelBookingPanel
                            base={`/organizations/${organizationId}/bookings/${b.id}`}
                            timezone={zone}
                            organizer
                            onCancelled={() => void load()}
                          />
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              )}

              {canManage && occurrence?.status === "SCHEDULED" && (
                <section aria-labelledby="cancel-date-heading" className="flex flex-col gap-2 text-sm">
                  <h2 id="cancel-date-heading" className="text-lg font-semibold">
                    Cancel this date
                  </h2>
                  <p className="text-zinc-600 dark:text-zinc-400">
                    Stops new applications and holds at once. Every booking is cancelled and refunded (what the vendor paid
                    minus Vendi&apos;s service fee); open checkouts are closed. Refunds may take a while to settle.
                  </p>
                  {!confirmDate ? (
                    <div>
                      <button type="button" onClick={() => setConfirmDate(true)} className={secondaryButtonClass}>
                        Cancel this date…
                      </button>
                    </div>
                  ) : (
                    <>
                      <label className="flex flex-col gap-1">
                        <span>Message to vendors</span>
                        <textarea
                          value={dateMessage}
                          onChange={(e) => setDateMessage(e.target.value)}
                          maxLength={500}
                          rows={2}
                          className={inputClass}
                        />
                      </label>
                      <div className="flex flex-wrap gap-2">
                        <button type="button" onClick={cancelDate} disabled={busy} className={buttonClass}>
                          {busy ? "Cancelling…" : `Cancel the date and ${bookings.length} booking${bookings.length === 1 ? "" : "s"}`}
                        </button>
                        <button type="button" onClick={() => setConfirmDate(false)} disabled={busy} className={secondaryButtonClass}>
                          Keep the date
                        </button>
                      </div>
                    </>
                  )}
                </section>
              )}
            </>
          )}
        </AccountGate>
      </div>
    </DjangoPage>
  );
}
