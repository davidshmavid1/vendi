import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { VendorApplicationDetail } from "./VendorApplicationDetail";

export const metadata: Metadata = { title: "Application · Vendi" };

export default async function Page({ params }: { params: Promise<{ businessId: string; applicationId: string }> }) {
  const { businessId, applicationId } = await params;
  if (!/^\d+$/.test(businessId) || !/^\d+$/.test(applicationId)) notFound();
  return <VendorApplicationDetail businessId={Number(businessId)} applicationId={Number(applicationId)} />;
}
