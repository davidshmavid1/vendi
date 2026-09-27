import type { Metadata } from "next";
import { safeNext } from "@/lib/applications/logic";
import { LoginForm } from "./LoginForm";

export const metadata: Metadata = { title: "Sign in · Vendi" };

export default async function LoginPage({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const { next } = await searchParams;
  return <LoginForm next={safeNext(next)} />;
}
