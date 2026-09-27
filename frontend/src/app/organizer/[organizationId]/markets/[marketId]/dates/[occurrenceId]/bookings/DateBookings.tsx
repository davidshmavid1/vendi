"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui";
import { AccountGate, DjangoPage, Notice } from "@/components/DjangoPage";
import { apiGet, type ApiError } from "@/lib/django/client";
import { useAccount } from "@/lib/django/useAccount";
import { formatDateTime } from "@/lib/applications/logic";
import { formatMinor } from "@/lib/layouts/money";
import type { Booking, BookingPage } from "@/lib/payments/types";

type Props = { organizationId: number; marketId: number; occurrenceId: number };

/** Confirmed stall bookings for one date (read-only, any team member). */
export function DateBookings({ organizationId, marketId, occurrenceId }: Props) {
  const { state: account, reload } = useAccount();
  const signedIn = account.status === "signed_in";
  const [bookings, setBookings] = useState<Booking[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    (async () => {
      const items: Booking[] = [];
      let cursor: number | null = null;
      do {
        const query: string = `occurrence_id=${occurrenceId}${cursor ? `&cursor=${cursor}` : ""}`;
        const result = await apiGet<BookingPage>(`/organizations/${organizationId}/bookings?${query}`);
        if (cancelled) return;
        if (!result.ok) return setError(result.error);
        items.push(...result.data.items);
        cursor = result.data.next_cursor;
      } while (cursor);
      setBookings(items);
    })();
    return () => {
      cancelled = true;
    };
  }, [signedIn, organizationId, occurrenceId]);

  const date = bookings?.[0]?.occurrence;
  return (
    <DjangoPage account={account}>
      <Link href={`/organizer/${organizationId}/markets/${marketId}`} className="text-sm text-zinc-600 underline dark:text-zinc-400">
        ← Market
      </Link>
      <h1 className="mt-4 text-2xl font-semibold tracking-tight">Bookings for this date</h1>
      {date && (
        <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
          {date.market_name} · {formatDateTime(date.starts_at, date.timezone)}
        </p>
      )}
      <div className="mt-6">
        <AccountGate account={account} retry={reload}>
          {error ? (
            <Notice tone="red">{error.message}</Notice>
          ) : !bookings ? (
            <p className="text-sm text-zinc-500">Loading…</p>
          ) : bookings.length === 0 ? (
            <Notice>No stalls are booked for this date yet.</Notice>
          ) : (
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Booked stalls</caption>
              <thead className="text-xs text-zinc-500">
                <tr>
                  <th scope="col" className="py-2 pr-4 font-medium">Stall</th>
                  <th scope="col" className="py-2 pr-4 font-medium">Vendor</th>
                  <th scope="col" className="py-2 pr-4 font-medium">Price</th>
                  <th scope="col" className="py-2 font-medium">Payment</th>
                </tr>
              </thead>
              <tbody>
                {bookings.map((b) => (
                  <tr key={b.id} className="border-t border-zinc-200 dark:border-zinc-800">
                    <th scope="row" className="py-2 pr-4 font-medium">{b.stall.label}</th>
                    <td className="py-2 pr-4">{b.vendor_business.name}</td>
                    <td className="py-2 pr-4">{formatMinor(b.price_minor, b.currency, b.currency_exponent)}</td>
                    <td className="py-2">
                      {b.payment_required ? <Badge tone="green">Paid</Badge> : <Badge>Free</Badge>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </AccountGate>
      </div>
    </DjangoPage>
  );
}
