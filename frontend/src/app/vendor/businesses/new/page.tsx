import type { Metadata } from "next";
import { safeNext } from "@/lib/applications/logic";
import { NewBusiness } from "./NewBusiness";

export const metadata: Metadata = { title: "Create a vendor business · Vendi" };

export default async function NewBusinessPage({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const { next } = await searchParams;
  return <NewBusiness next={safeNext(next, "/vendor/applications")} />;
}
