"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Badge, Card } from "@/components/ui";
import { AccountGate, buttonClass, DjangoPage, Notice, secondaryButtonClass } from "@/components/DjangoPage";
import { PlanCanvas } from "@/components/layouts/PlanCanvas";
import { apiGet, apiSend, type ApiError } from "@/lib/django/client";
import { useAccount } from "@/lib/django/useAccount";
import type { MyBusiness, Page, VendorApplication } from "@/lib/applications/types";
import { formatDateTime } from "@/lib/applications/logic";
import { formatMinor } from "@/lib/layouts/money";
import { physicalSize, type PublicLayout } from "@/lib/layouts/types";
import { clockOffsetMs, formatCountdown, newRequestKey, secondsLeft } from "@/lib/reservations/logic";
import type { Availability, Reservation } from "@/lib/reservations/types";

type Props = { businessId: number; applicationId: number };
type Attempt = { offerId: number; key: string };

const REFRESH_MS = 20_000;

/** Choose and hold one stall for an approved application's date. A hold is
 *  temporary; it isn't a booking and nothing is paid here. */
export function StallPicker({ businessId, applicationId }: Props) {
  const { state: account, reload } = useAccount();
  const signedIn = account.status === "signed_in";
  const base = `/vendors/${businessId}/reservations`;
  const [application, setApplication] = useState<VendorApplication | null>(null);
  const [role, setRole] = useState<MyBusiness["role"] | null>(null);
  const [layout, setLayout] = useState<PublicLayout | null>(null);
  const [availability, setAvailability] = useState<Availability | null>(null);
  const [hold, setHold] = useState<Reservation | null>(null);
  const [offset, setOffset] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [message, setMessage] = useState<{ tone: "red" | "amber" | "green"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  // The stall being held right now, and an attempt awaiting retry (a retry
  // reuses its key).
  const [pendingOfferId, setPendingOfferId] = useState<number | null>(null);
  const [retryable, setRetryable] = useState<Attempt | null>(null);

  const occurrenceId = application?.occurrence.id;

  /** Current live hold (or confirmed stall) for this date, from the server. */
  const refreshHold = useCallback(async () => {
    if (!occurrenceId) return;
    const received = Date.now();
    const result = await apiGet<Page<Reservation>>(`${base}?occurrence_id=${occurrenceId}`);
    if (!result.ok) return;
    const live = result.data.items.find((r) => r.status === "HELD" || r.status === "CONFIRMED") ?? null;
    setHold(live);
    if (live) setOffset(clockOffsetMs(live.server_time, received));
  }, [base, occurrenceId]);

  const refreshAvailability = useCallback(async () => {
    if (!occurrenceId) return;
    const result = await apiGet<Availability>(`/public/occurrences/${occurrenceId}/stall-availability`);
    if (result.ok) setAvailability(result.data);
  }, [occurrenceId]);

  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    (async () => {
      const [app, membership] = await Promise.all([
        apiGet<VendorApplication>(`/vendors/${businessId}/applications/${applicationId}`),
        apiGet<MyBusiness>(`/vendors/${businessId}`),
      ]);
      if (cancelled) return;
      if (membership.ok) setRole(membership.data.role);
      if (!app.ok) return setLoadError(app.error);
      setApplication(app.data);
      const plan = await apiGet<PublicLayout>(`/public/occurrences/${app.data.occurrence.id}/layout`);
      if (cancelled) return;
      if (plan.ok) setLayout(plan.data);
      else if (plan.status !== 404) setLoadError(plan.error);
    })();
    return () => {
      cancelled = true;
    };
  }, [signedIn, businessId, applicationId]);

  // Recover any existing hold (e.g. after a refresh) and keep availability fresh.
  useEffect(() => {
    if (!occurrenceId) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch for this date
    void refreshHold();
    void refreshAvailability();
    const timer = window.setInterval(() => void refreshAvailability(), REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [occurrenceId, refreshHold, refreshAvailability]);

  // Tick the countdown; when it reaches zero, ask the server what's true now.
  const remaining = hold?.status === "HELD" ? secondsLeft(hold.expires_at, offset, now) : null;
  useEffect(() => {
    if (hold?.status !== "HELD") return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [hold]);
  useEffect(() => {
    if (remaining !== 0) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the hold just ran out
    setMessage({ tone: "amber", text: "Your hold expired, so the stall is free for others again. You can choose a stall again." });
    setHold(null);
    void refreshHold();
    void refreshAvailability();
  }, [remaining, refreshHold, refreshAvailability]);

  async function takeHold(offerId: number, retry?: Attempt) {
    if (busy) return;
    const current = retry ?? { offerId, key: newRequestKey() };
    setPendingOfferId(current.offerId);
    setBusy(true);
    setMessage(null);
    setRetryable(null);
    const received = Date.now();
    const result = await apiSend<Reservation>("POST", base, { offer_id: current.offerId, request_key: current.key });
    setBusy(false);
    setPendingOfferId(null);
    if (result.ok) {
      setOffset(clockOffsetMs(result.data.server_time, received));
      setNow(Date.now());
      if (result.data.status === "HELD") {
        setHold(result.data);
        setSelected(String(result.data.stall.id));
        setMessage({ tone: "green", text: `Stall ${result.data.stall.label} is held for you.` });
      } else {
        // A retry of an attempt that has since expired or been released: never shown as held.
        setHold(null);
        setMessage({ tone: "amber", text: "That hold is no longer active. Choose a stall again." });
      }
      void refreshAvailability();
      return;
    }
    if (result.status === 0) {
      // We don't know whether the hold was taken: retry with the same key, which
      // returns the original instead of taking a second one.
      setRetryable(current);
      setMessage({ tone: "red", text: "We couldn't reach Vendi, so we don't know if the stall was held." });
      return;
    }
    const code = result.error.code;
    const text =
      code === "stall_unavailable"
        ? "Someone else just took that stall. Choose another."
        : code === "hold_exists"
          ? "You already hold a stall for this date. Release it first to choose another."
          : result.status === 429
            ? "Too many attempts. Wait a little and try again."
            : result.error.message;
    setMessage({ tone: "red", text });
    void refreshAvailability();
    if (code === "hold_exists") void refreshHold();
  }

  async function release() {
    if (!hold || busy) return;
    if (!window.confirm(`Release stall ${hold.stall.label}? Someone else may take it right away.`)) return;
    setBusy(true);
    const result = await apiSend<Reservation>("POST", `${base}/${hold.id}/release`);
    setBusy(false);
    if (result.ok) {
      setHold(null);
      setMessage({ tone: "green", text: `Stall ${result.data.stall.label} released.` });
    } else {
      setMessage({ tone: "red", text: result.error.message });
    }
    void refreshHold();
    void refreshAvailability();
  }

  const statusFor = new Map(availability?.items.map((i) => [i.stall_id, i.status]) ?? []);
  const isOwner = role === "OWNER";
  const approved = application?.status === "APPROVED";
  const planStalls = (layout?.stalls ?? []).map((s) => ({
    key: String(s.id),
    label: s.label,
    x: s.x,
    y: s.y,
    width: s.width,
    height: s.height,
    muted: hold?.stall.id !== s.id && statusFor.get(s.id) !== "available",
  }));

  return (
    <DjangoPage account={account}>
      <Link
        href={`/vendor/businesses/${businessId}/applications/${applicationId}`}
        className="text-sm text-zinc-600 underline dark:text-zinc-400"
      >
        ← Application
      </Link>
      <h1 className="mt-4 text-2xl font-semibold tracking-tight">Choose a stall</h1>
      {application && (
        <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
          {application.occurrence.market_name} · {formatDateTime(application.occurrence.starts_at, application.occurrence.timezone)}
        </p>
      )}
      <div className="mt-6">
        <AccountGate account={account} retry={reload}>
          {loadError ? (
            <Notice tone="red">{loadError.message}</Notice>
          ) : !application ? (
            <p className="text-sm text-zinc-500">Loading…</p>
          ) : !approved ? (
            <Notice tone="amber">You can choose a stall once this application is approved.</Notice>
          ) : !layout ? (
            <Notice>The organizer hasn&apos;t published stalls for this date yet.</Notice>
          ) : (
            <div className="flex flex-col gap-4">
              {hold && (
                <Card className="!p-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <p className="font-medium">
                        Stall {hold.stall.label} ·{" "}
                        {formatMinor(hold.price_minor, hold.currency, hold.currency_exponent)}
                      </p>
                      {hold.status === "HELD" ? (
                        <p className="text-sm" aria-live="polite">
                          Held for you for <span className="font-mono font-semibold">{formatCountdown(remaining ?? 0)}</span>{" "}
                          (until {formatDateTime(hold.expires_at, hold.occurrence.timezone)})
                        </p>
                      ) : (
                        <Badge tone="green">Confirmed</Badge>
                      )}
                    </div>
                    {hold.status === "HELD" && isOwner && (
                      <button type="button" onClick={release} disabled={busy} className={secondaryButtonClass}>
                        Release this stall
                      </button>
                    )}
                  </div>
                  {hold.status === "HELD" && (
                    <p className="mt-3 text-xs text-zinc-500">
                      This is a temporary hold, not a booking: the stall is set aside for you until the timer ends, then
                      released automatically. Paying for and confirming stalls isn&apos;t available on Vendi yet.
                    </p>
                  )}
                </Card>
              )}
              {message && <Notice tone={message.tone}>{message.text}</Notice>}
              {retryable && (
                <div>
                  <button type="button" onClick={() => takeHold(retryable.offerId, retryable)} disabled={busy} className={buttonClass}>
                    Try again
                  </button>
                </div>
              )}
              {!isOwner && role && (
                <Notice>You can see your business&apos;s stall here. Only the business owner can hold or release a stall.</Notice>
              )}

              <Card className="!p-3">
                <PlanCanvas
                  stalls={planStalls}
                  canvasWidth={layout.canvas_width}
                  canvasHeight={layout.canvas_height}
                  selected={hold ? String(hold.stall.id) : selected}
                  invalid={new Set()}
                  editable={false}
                  onSelect={setSelected}
                  onMove={() => {}}
                />
                <p className="mt-2 text-xs text-zinc-500">
                  Green stalls are available. Grey ones are taken or not offered. Availability refreshes every 20 seconds.
                </p>
              </Card>

              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <caption className="sr-only">Stalls for this date</caption>
                  <thead className="text-xs text-zinc-500">
                    <tr>
                      <th scope="col" className="py-2 pr-4 font-medium">Stall</th>
                      <th scope="col" className="py-2 pr-4 font-medium">Size</th>
                      <th scope="col" className="py-2 pr-4 font-medium">Price</th>
                      <th scope="col" className="py-2 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {layout.stalls.map((s) => {
                      const status = statusFor.get(s.id);
                      const mine = hold?.stall.id === s.id;
                      return (
                        <tr
                          key={s.id}
                          className={`border-t border-zinc-200 dark:border-zinc-800 ${
                            selected === String(s.id) || mine ? "bg-orange-50 dark:bg-zinc-800" : ""
                          }`}
                        >
                          <th scope="row" className="py-2 pr-4 font-medium">
                            {s.label}
                          </th>
                          <td className="py-2 pr-4">{physicalSize(s) ?? "—"}</td>
                          <td className="py-2 pr-4">
                            {s.offered && s.price_minor !== null
                              ? formatMinor(s.price_minor, layout.currency, layout.currency_exponent)
                              : "—"}
                          </td>
                          <td className="py-2">
                            {mine ? (
                              <Badge tone="blue">{hold?.status === "CONFIRMED" ? "Yours" : "Held by you"}</Badge>
                            ) : status === "available" ? (
                              isOwner && !hold && s.offer_id !== null ? (
                                <button
                                  type="button"
                                  onClick={() => takeHold(s.offer_id as number)}
                                  disabled={busy}
                                  className="whitespace-nowrap rounded-md border border-zinc-300 px-3 py-1 text-xs font-medium hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-800"
                                  aria-label={`Hold stall ${s.label}`}
                                >
                                  {pendingOfferId === s.offer_id ? "Holding…" : "Hold this stall"}
                                </button>
                              ) : (
                                <span className="text-green-700 dark:text-green-400">Available</span>
                              )
                            ) : status === "unavailable" ? (
                              <span className="text-zinc-500">Taken</span>
                            ) : (
                              <span className="text-zinc-500">Not offered</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </AccountGate>
      </div>
    </DjangoPage>
  );
}
