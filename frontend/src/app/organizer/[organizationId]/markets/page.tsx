import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { OrganizerMarkets } from "./OrganizerMarkets";

export const metadata: Metadata = { title: "Markets · Vendi" };

export default async function Page({ params }: { params: Promise<{ organizationId: string }> }) {
  const { organizationId } = await params;
  if (!/^\d+$/.test(organizationId)) notFound();
  return <OrganizerMarkets organizationId={Number(organizationId)} />;
}
