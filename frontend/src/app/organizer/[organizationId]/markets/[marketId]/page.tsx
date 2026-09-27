import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { MarketDates } from "./MarketDates";

export const metadata: Metadata = { title: "Market dates · Vendi" };

export default async function Page({ params }: { params: Promise<{ organizationId: string; marketId: string }> }) {
  const { organizationId, marketId } = await params;
  if (!/^\d+$/.test(organizationId) || !/^\d+$/.test(marketId)) notFound();
  return <MarketDates organizationId={Number(organizationId)} marketId={Number(marketId)} />;
}
