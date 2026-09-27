import type { Metadata } from "next";
import { safeNext } from "@/lib/applications/logic";
import { RegisterForm } from "./RegisterForm";

export const metadata: Metadata = { title: "Create an account · Vendi" };

export default async function RegisterPage({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const { next } = await searchParams;
  return <RegisterForm next={safeNext(next)} />;
}
