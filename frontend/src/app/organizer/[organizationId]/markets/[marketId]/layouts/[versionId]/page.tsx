import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { VersionEditor } from "./VersionEditor";

export const metadata: Metadata = { title: "Stall layout · Vendi" };

export default async function Page({
  params,
}: {
  params: Promise<{ organizationId: string; marketId: string; versionId: string }>;
}) {
  const { organizationId, marketId, versionId } = await params;
  if (![organizationId, marketId, versionId].every((v) => /^\d+$/.test(v))) notFound();
  return <VersionEditor organizationId={Number(organizationId)} marketId={Number(marketId)} versionId={Number(versionId)} />;
}
