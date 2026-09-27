import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { LayoutEditor } from "./LayoutEditor";

export const metadata: Metadata = { title: "Stall layout · Vendi" };

export default async function Page({
  params,
}: {
  params: Promise<{ organizationId: string; marketId: string; occurrenceId: string }>;
}) {
  const { organizationId, marketId, occurrenceId } = await params;
  if (![organizationId, marketId, occurrenceId].every((v) => /^\d+$/.test(v))) notFound();
  return (
    <LayoutEditor organizationId={Number(organizationId)} marketId={Number(marketId)} occurrenceId={Number(occurrenceId)} />
  );
}
