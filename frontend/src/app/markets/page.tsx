import type { Metadata } from "next";
import { connection } from "next/server";
import { Suspense } from "react";
import { djangoOrigin } from "@/lib/djangoApi";
import { DiscoveryClient } from "./DiscoveryClient";

export const metadata: Metadata = {
  title: "Find markets · Vendi",
  description: "Browse farmers markets and popup events with upcoming dates.",
};

export default async function MarketsPage() {
  // Read configuration per request, not at build time.
  await connection();
  const available = djangoOrigin() !== null;
  const tileUrl = process.env.MAP_TILE_URL || null;
  const attribution = process.env.MAP_TILE_ATTRIBUTION || "";

  return (
    <main className="mx-auto flex w-full max-w-7xl flex-1 flex-col px-4 py-6 sm:px-6">
      <h1 className="text-2xl font-semibold tracking-tight">Find a market</h1>
      <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
        Farmers markets and popups with upcoming dates. No account needed.
      </p>
      {available ? (
        <Suspense fallback={<p className="mt-6 text-sm text-zinc-500">Loading markets…</p>}>
          <DiscoveryClient map={tileUrl ? { tileUrl, attribution } : null} />
        </Suspense>
      ) : (
        <p className="mt-6 rounded-md border border-zinc-200 bg-white p-4 text-sm dark:border-zinc-800 dark:bg-zinc-900">
          Market discovery isn&apos;t available here yet.
        </p>
      )}
    </main>
  );
}
