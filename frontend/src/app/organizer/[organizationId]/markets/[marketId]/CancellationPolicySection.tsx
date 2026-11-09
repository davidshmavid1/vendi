"use client";

import { useState } from "react";
import { buttonClass, inputClass, Notice } from "@/components/DjangoPage";
import { apiSend } from "@/lib/django/client";

type Props = {
  organizationId: number;
  marketId: number;
  initialHours: number | null;
  canEdit: boolean;
};

/** Vendor cancellation cutoff for new holds. Existing bookings keep theirs. */
export function CancellationPolicySection({ organizationId, marketId, initialHours, canEdit }: Props) {
  const [hours, setHours] = useState(initialHours === null ? "" : String(initialHours));
  const [saved, setSaved] = useState<number | null>(initialHours);
  const [message, setMessage] = useState<{ tone: "red" | "green"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function save() {
    const value = hours.trim() === "" ? null : Number(hours);
    if (value !== null && (!Number.isInteger(value) || value < 0 || value > 8760)) {
      setMessage({ tone: "red", text: "Enter whole hours from 0 to 8760, or leave it empty." });
      return;
    }
    setBusy(true);
    setMessage(null);
    const result = await apiSend<{ vendor_cancellation_cutoff_hours: number | null }>(
      "PUT",
      `/organizations/${organizationId}/markets/${marketId}/cancellation-policy`,
      { vendor_cancellation_cutoff_hours: value },
    );
    setBusy(false);
    if (result.ok) {
      setSaved(result.data.vendor_cancellation_cutoff_hours);
      setMessage({ tone: "green", text: "Saved. It applies to stalls held from now on." });
    } else {
      setMessage({ tone: "red", text: result.error.message });
    }
  }

  return (
    <section aria-labelledby="cancel-policy-heading">
      <h2 id="cancel-policy-heading" className="text-lg font-semibold">
        Vendor cancellations
      </h2>
      <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
        {saved === null
          ? "Vendors can't cancel bookings themselves; they contact you."
          : `Vendors can cancel until ${saved} hour${saved === 1 ? "" : "s"} before a date starts, for a refund of what they paid minus Vendi's service fee. After that, they contact you.`}{" "}
        Changes apply to new bookings only; existing bookings keep the terms they were made under.
      </p>
      {canEdit && (
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span>Cutoff (hours before the date starts)</span>
            <input
              type="number"
              min={0}
              max={8760}
              inputMode="numeric"
              value={hours}
              onChange={(e) => setHours(e.target.value)}
              placeholder="Not allowed"
              className={`${inputClass} w-40`}
            />
          </label>
          <button type="button" onClick={save} disabled={busy} className={buttonClass}>
            Save
          </button>
        </div>
      )}
      {message && (
        <div className="mt-3">
          <Notice tone={message.tone}>{message.text}</Notice>
        </div>
      )}
    </section>
  );
}
