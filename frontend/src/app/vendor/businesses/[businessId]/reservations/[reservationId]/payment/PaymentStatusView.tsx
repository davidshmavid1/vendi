"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Card } from "@/components/ui";
import { AccountGate, buttonClass, DjangoPage, Notice, secondaryButtonClass } from "@/components/DjangoPage";
import { apiGet, apiSend, type ApiError, type ApiResult } from "@/lib/django/client";
import { useAccount } from "@/lib/django/useAccount";
import { formatDateTime } from "@/lib/applications/logic";
import { formatMinor } from "@/lib/layouts/money";
import { isCheckoutUrl, isSettling, pollDelayMs } from "@/lib/payments/logic";
import type { PaymentStatus } from "@/lib/payments/types";

type Props = { businessId: number; reservationId: number; returned: boolean; cancelled: boolean };

/** Where a vendor lands after Stripe Checkout (and can come back to later).
 *  Everything shown comes from the backend; the URL never confirms payment. */
export function PaymentStatusView({ businessId, reservationId, returned, cancelled }: Props) {
  const { state: account, reload } = useAccount();
  const signedIn = account.status === "signed_in";
  const base = `/vendors/${businessId}/reservations/${reservationId}`;
  const [status, setStatus] = useState<PaymentStatus | null>(null);
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [message, setMessage] = useState<{ tone: "red" | "amber" | "green"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [polls, setPolls] = useState(0);

  const apply = useCallback((result: ApiResult<PaymentStatus>) => {
    if (result.ok) {
      setStatus(result.data);
      setLoadError(null);
    } else if (result.status === 0) {
      setMessage({ tone: "red", text: "We couldn't reach Vendi. Check your connection and try again." });
    } else {
      setLoadError(result.error);
    }
    return result;
  }, []);

  const load = useCallback(async () => apply(await apiGet<PaymentStatus>(`${base}/payment`)), [apply, base]);

  useEffect(() => {
    if (!signedIn) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch
    void load();
  }, [signedIn, load]);

  // Bounded polling while the outcome is being decided.
  const settling = status ? isSettling(status.state, returned) : false;
  const delay = settling ? pollDelayMs(polls) : null;
  useEffect(() => {
    if (delay === null) return;
    const timer = window.setTimeout(() => {
      setPolls((n) => n + 1);
      void load();
    }, delay);
    return () => window.clearTimeout(timer);
  }, [delay, polls, load]);

  async function run(path: string, onOk?: (data: PaymentStatus) => void) {
    if (busy) return;
    setBusy(true);
    setMessage(null);
    const result = await apiSend<PaymentStatus>("POST", `${base}${path}`);
    setBusy(false);
    if (result.ok) {
      setStatus(result.data);
      onOk?.(result.data);
    } else if (result.status === 0 || result.status === 503) {
      setMessage({
        tone: "amber",
        text: "We couldn't get an answer from the payment provider. Nothing was charged twice; try again in a moment.",
      });
    } else {
      setMessage({ tone: "red", text: result.error.message });
    }
  }

  function payNow() {
    void run("/checkout", (data) => {
      const url = data.payment?.checkout_url;
      if (isCheckoutUrl(url)) window.location.assign(url);
    });
  }

  function cancel() {
    if (!window.confirm("Cancel this checkout and release the stall? Someone else may take it right away.")) return;
    void run("/checkout/cancel");
  }

  function checkAgain() {
    setPolls(0);
    void run("/payment/check");
  }

  const reservation = status?.reservation;
  const booking = status?.booking;
  const stallPage = reservation
    ? `/vendor/businesses/${businessId}/applications/${reservation.application_id}/stall`
    : null;

  return (
    <DjangoPage account={account}>
      {stallPage && (
        <Link href={stallPage} className="text-sm text-zinc-600 underline dark:text-zinc-400">
          ← Stalls for this date
        </Link>
      )}
      <h1 className="mt-4 text-2xl font-semibold tracking-tight">Stall payment</h1>
      {reservation && (
        <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
          Stall {reservation.stall.label} · {reservation.occurrence.market_name} ·{" "}
          {formatDateTime(reservation.occurrence.starts_at, reservation.occurrence.timezone)}
        </p>
      )}
      <div className="mt-6 flex flex-col gap-4">
        <AccountGate account={account} retry={reload}>
          {loadError ? (
            <Notice tone="red">{loadError.message}</Notice>
          ) : !status || !reservation ? (
            <p className="text-sm text-zinc-500">Loading…</p>
          ) : (
            <>
              <Card className="!p-4">
                <p className="font-medium">
                  {status.payment_required
                    ? formatMinor(reservation.price_minor, reservation.currency, reservation.currency_exponent)
                    : "Free stall"}
                </p>
                <div className="mt-2 text-sm" aria-live="polite">
                  <StateText status={status} returned={returned} cancelled={cancelled} stalled={settling && delay === null} />
                </div>
              </Card>

              {booking && (
                <Card className="!p-4">
                  <h2 className="font-medium">Booking #{booking.id}</h2>
                  <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
                    <dt className="text-zinc-500">Stall</dt>
                    <dd>{booking.stall.label}</dd>
                    <dt className="text-zinc-500">Date</dt>
                    <dd>{formatDateTime(booking.occurrence.starts_at, booking.occurrence.timezone)}</dd>
                    <dt className="text-zinc-500">Business</dt>
                    <dd>{booking.vendor_business.name}</dd>
                    <dt className="text-zinc-500">{booking.payment_required ? "Paid" : "Price"}</dt>
                    <dd>
                      {booking.payment_required
                        ? `${formatMinor(booking.price_minor, booking.currency, booking.currency_exponent)}${
                            booking.paid_at ? ` on ${formatDateTime(booking.paid_at, booking.occurrence.timezone)}` : ""
                          }`
                        : "Free"}
                    </dd>
                  </dl>
                </Card>
              )}

              {message && <Notice tone={message.tone}>{message.text}</Notice>}

              <div className="flex flex-wrap gap-3">
                {status.state === "CHECKOUT_OPEN" && status.payment?.checkout_url && (
                  <button type="button" onClick={payNow} disabled={busy} className={buttonClass}>
                    Continue to payment
                  </button>
                )}
                {(status.state === "CHECKOUT_OPEN" || status.state === "PROCESSING") && (
                  <button type="button" onClick={checkAgain} disabled={busy} className={secondaryButtonClass}>
                    Check payment status
                  </button>
                )}
                {status.state === "CHECKOUT_OPEN" && status.payment?.checkout_url && (
                  <button type="button" onClick={cancel} disabled={busy} className={secondaryButtonClass}>
                    Cancel checkout and release stall
                  </button>
                )}
                {(status.state === "HOLDING" || status.state === "EXPIRED" || status.state === "RELEASED") && stallPage && (
                  <Link href={stallPage} className={secondaryButtonClass}>
                    {status.state === "HOLDING" ? "Back to your held stall" : "Choose a stall"}
                  </Link>
                )}
              </div>
            </>
          )}
        </AccountGate>
      </div>
    </DjangoPage>
  );
}

function StateText({
  status,
  returned,
  cancelled,
  stalled,
}: {
  status: PaymentStatus;
  returned: boolean;
  cancelled: boolean;
  stalled: boolean;
}) {
  switch (status.state) {
    case "BOOKED":
      return <p className="text-green-700 dark:text-green-400">Your stall is booked.</p>;
    case "PROCESSING":
      return stalled ? (
        <p>We&apos;re still waiting for the payment provider to confirm. You haven&apos;t been charged twice; check again in a little while.</p>
      ) : (
        <p>Confirming your payment… Your stall stays held meanwhile.</p>
      );
    case "CHECKOUT_OPEN":
      if (returned) {
        return stalled ? (
          <p>We haven&apos;t received your payment yet. If you paid, it can take a moment; check again. Otherwise, continue to payment.</p>
        ) : (
          <p>Checking for your payment…</p>
        );
      }
      return (
        <p>
          {cancelled ? "You left the payment page. " : ""}Checkout is still open until{" "}
          {status.payment ? formatDateTime(status.payment.session_expires_at, status.reservation.occurrence.timezone) : "it expires"}
          ; your stall stays held until then.
        </p>
      );
    case "REFUND_PENDING":
      return <p>Your payment arrived after the stall could no longer be booked for you, so we&apos;re refunding it in full.</p>;
    case "REFUNDED":
      return <p>Your payment was refunded in full because the stall couldn&apos;t be booked for you.</p>;
    case "REFUND_FAILED":
      return <p className="text-red-700 dark:text-red-400">We couldn&apos;t refund your payment automatically. Vendi support will fix this; you don&apos;t need to pay again.</p>;
    case "HOLDING":
      return <p>This stall is held for you but not paid yet.</p>;
    case "RELEASED":
      return <p>This stall was released and isn&apos;t booked.</p>;
    default:
      return <p>This hold expired without a payment, so the stall isn&apos;t booked.</p>;
  }
}
