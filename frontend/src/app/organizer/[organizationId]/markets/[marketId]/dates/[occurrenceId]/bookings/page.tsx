import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { DateBookings } from "./DateBookings";

export const metadata: Metadata = { title: "Bookings · Vendi" };

export default async function Page({
  params,
}: {
  params: Promise<{ organizationId: string; marketId: string; occurrenceId: string }>;
}) {
  const { organizationId, marketId, occurrenceId } = await params;
  if (![organizationId, marketId, occurrenceId].every((v) => /^\d+$/.test(v))) notFound();
  return (
    <DateBookings organizationId={Number(organizationId)} marketId={Number(marketId)} occurrenceId={Number(occurrenceId)} />
  );
}
