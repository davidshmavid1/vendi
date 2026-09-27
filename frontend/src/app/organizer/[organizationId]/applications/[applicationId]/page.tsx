import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ReviewApplication } from "./ReviewApplication";

export const metadata: Metadata = { title: "Review application · Vendi" };

export default async function Page({ params }: { params: Promise<{ organizationId: string; applicationId: string }> }) {
  const { organizationId, applicationId } = await params;
  if (!/^\d+$/.test(organizationId) || !/^\d+$/.test(applicationId)) notFound();
  return <ReviewApplication organizationId={Number(organizationId)} applicationId={Number(applicationId)} />;
}
