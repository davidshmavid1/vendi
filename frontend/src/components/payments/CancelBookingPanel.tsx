"use client";

import { useCallback, useEffect, useState } from "react";
import { buttonClass, inputClass, Notice, secondaryButtonClass } from "@/components/DjangoPage";
import { apiGet, apiSend } from "@/lib/django/client";
import { formatDateTime } from "@/lib/applications/logic";
import { formatMinor } from "@/lib/layouts/money";
import type { Booking, CancellationPreview } from "@/lib/payments/types";

type Props = {
  /** e.g. /vendors/1/bookings/2 or /organizations/1/bookings/2 */
  base: string;
  timezone: string;
  organizer?: boolean;
  onCancelled: (booking: Booking) => void;
};

/** Cancel a booking: server-computed terms first, then explicit confirmation.
 *  The previewed refund is sent back so a changed outcome is refused. */
export function CancelBookingPanel({ base, timezone, organizer = false, onCancelled }: Props) {
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState<CancellationPreview | null>(null);
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [message, setMessage] = useState<{ tone: "red" | "amber"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const result = await apiGet<CancellationPreview>(`${base}/cancellation`);
    if (result.ok) setPreview(result.data);
    else setMessage({ tone: "red", text: result.status === 0 ? "We couldn't reach Vendi." : result.error.message });
  }, [base]);

  useEffect(() => {
    if (!open) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch terms when opened
    void load();
  }, [open, load]);

  async function confirm() {
    if (!preview || busy) return;
    setBusy(true);
    setMessage(null);
    const result = await apiSend<Booking>("POST", `${base}/cancel`, {
      expected_refund_minor: preview.refund_minor,
      currency: preview.currency,
      reason,
      ...(organizer ? { internal_note: note } : {}),
    });
    setBusy(false);
    if (result.ok) {
      setOpen(false);
      onCancelled(result.data);
    } else if (result.status === 0) {
      setMessage({ tone: "amber", text: "We couldn't reach Vendi. Check the booking before trying again." });
    } else {
      setMessage({ tone: "red", text: result.error.message });
      setAgreed(false);
      void load(); // terms may have changed (e.g. the deadline passed)
    }
  }

  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} className={secondaryButtonClass}>
        Cancel booking…
      </button>
    );
  }

  const money = (minor: number) => (preview ? formatMinor(minor, preview.currency, preview.currency_exponent) : "");
  const needsReason = organizer && reason.trim() === "";

  return (
    <div className="flex flex-col gap-3 rounded-md border border-zinc-200 p-4 text-sm dark:border-zinc-800">
      <h3 className="font-medium">Cancel this booking</h3>
      {!preview ? (
        <p className="text-zinc-500">Loading terms…</p>
      ) : !preview.permitted ? (
        <Notice tone="amber">{preview.message}</Notice>
      ) : !preview.can_act ? (
        <Notice>Only {organizer ? "an owner or admin" : "the business owner"} can cancel this booking.</Notice>
      ) : (
        <>
          <ul className="list-disc pl-5">
            <li>The stall is released and may be booked by someone else.</li>
            {preview.refund_rule === "NO_PAYMENT" ? (
              <li>This booking was free, so there&apos;s nothing to refund.</li>
            ) : (
              <li>
                Refund: <strong>{money(preview.refund_minor)}</strong> of the {money(preview.paid_minor)} paid. Vendi&apos;s
                service fee ({money(preview.fee_retained_minor)}) isn&apos;t refunded. Refunds usually reach the card within
                several business days; we can&apos;t promise an exact date.
              </li>
            )}
            {!organizer && preview.vendor_deadline && (
              <li>You can cancel until {formatDateTime(preview.vendor_deadline, timezone)}.</li>
            )}
          </ul>
          <label className="flex flex-col gap-1">
            <span>{organizer ? "Reason (shown to the vendor)" : "Reason (optional, shared with the organizer)"}</span>
            <textarea value={reason} onChange={(e) => setReason(e.target.value)} maxLength={1000} rows={2} className={inputClass} />
          </label>
          {organizer && (
            <label className="flex flex-col gap-1">
              <span>Internal note (only your team sees it)</span>
              <textarea value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} rows={2} className={inputClass} />
            </label>
          )}
          <label className="flex items-start gap-2">
            <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} className="mt-1" />
            <span>
              I understand this can&apos;t be undone
              {preview.refund_rule === "PAID_MINUS_FEE" ? ` and the refund is ${money(preview.refund_minor)}.` : "."}
            </span>
          </label>
        </>
      )}
      {message && <Notice tone={message.tone}>{message.text}</Notice>}
      <div className="flex flex-wrap gap-2">
        {preview?.permitted && preview.can_act && (
          <button type="button" onClick={confirm} disabled={busy || !agreed || needsReason} className={buttonClass}>
            {busy ? "Cancelling…" : "Confirm cancellation"}
          </button>
        )}
        <button type="button" onClick={() => setOpen(false)} disabled={busy} className={secondaryButtonClass}>
          Keep booking
        </button>
      </div>
    </div>
  );
}
