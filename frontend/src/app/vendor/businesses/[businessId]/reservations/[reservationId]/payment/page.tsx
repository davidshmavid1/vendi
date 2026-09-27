import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PaymentStatusView } from "./PaymentStatusView";

export const metadata: Metadata = { title: "Stall payment · Vendi" };

export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ businessId: string; reservationId: string }>;
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const { businessId, reservationId } = await params;
  if (!/^\d+$/.test(businessId) || !/^\d+$/.test(reservationId)) notFound();
  // Only used to choose wording; the payment's state always comes from the API.
  const checkout = (await searchParams).checkout;
  return (
    <PaymentStatusView
      businessId={Number(businessId)}
      reservationId={Number(reservationId)}
      returned={checkout === "returned"}
      cancelled={checkout === "cancelled"}
    />
  );
}
