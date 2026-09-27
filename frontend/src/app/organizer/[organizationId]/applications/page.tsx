import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { OrganizerApplications } from "./OrganizerApplications";

export const metadata: Metadata = { title: "Applications · Vendi" };

export default async function Page({ params }: { params: Promise<{ organizationId: string }> }) {
  const { organizationId } = await params;
  if (!/^\d+$/.test(organizationId)) notFound();
  return <OrganizerApplications organizationId={Number(organizationId)} />;
}
