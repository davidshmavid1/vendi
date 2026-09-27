import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { StallPicker } from "./StallPicker";

export const metadata: Metadata = { title: "Choose a stall · Vendi" };

export default async function Page({ params }: { params: Promise<{ businessId: string; applicationId: string }> }) {
  const { businessId, applicationId } = await params;
  if (!/^\d+$/.test(businessId) || !/^\d+$/.test(applicationId)) notFound();
  return <StallPicker businessId={Number(businessId)} applicationId={Number(applicationId)} />;
}
