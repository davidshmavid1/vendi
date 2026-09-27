import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ApplyClient } from "./ApplyClient";

export const metadata: Metadata = { title: "Apply to a market date · Vendi" };

export default async function ApplyPage({
  params,
}: {
  params: Promise<{ marketId: string; occurrenceId: string }>;
}) {
  const { marketId, occurrenceId } = await params;
  if (!/^\d+$/.test(marketId) || !/^\d+$/.test(occurrenceId)) notFound();
  return <ApplyClient marketId={Number(marketId)} occurrenceId={Number(occurrenceId)} />;
}
