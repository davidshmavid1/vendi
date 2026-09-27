import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Badge, Card } from "@/components/ui";
import { getPublic } from "@/lib/djangoApi";
import { formatOccurrence } from "@/lib/discovery/logic";
import { intakeMessage, type IntakeState } from "@/lib/applications/logic";
import type { PublicLayout } from "@/lib/layouts/types";
import type { PublicOccurrence } from "@/lib/applications/types";
import { PublicLayoutView } from "./PublicLayoutView";

export const metadata: Metadata = { title: "Market date · Vendi" };

type OccurrenceDetail = PublicOccurrence & { market: { id: number; name: string; venue_name: string; city: string; region: string; organizer: { name: string } } };

export default async function DatePage({ params }: { params: Promise<{ marketId: string; occurrenceId: string }> }) {
  const { marketId, occurrenceId } = await params;
  if (!/^\d+$/.test(marketId) || !/^\d+$/.test(occurrenceId)) notFound();
  const occurrence = await getPublic<OccurrenceDetail>(`/occurrences/${occurrenceId}`);
  if (!occurrence.ok && occurrence.status === 404) notFound();
  if (occurrence.ok && String(occurrence.data.market_id) !== marketId) notFound();

  if (!occurrence.ok) {
    return (
      <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-10 sm:px-6">
        <p className="text-sm">
          {occurrence.status === 503 ? "Market details aren't available here yet." : "We couldn't load this date right now. Please try again."}
        </p>
      </main>
    );
  }
  const date = occurrence.data;
  const [layout, intake] = await Promise.all([
    getPublic<PublicLayout>(`/occurrences/${occurrenceId}/layout`),
    getPublic<{ state: IntakeState; opens_at: string | null }>(`/occurrences/${occurrenceId}/application`),
  ]);

  return (
    <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-10 sm:px-6">
      <Link href={`/markets/${marketId}`} className="text-sm text-zinc-600 underline dark:text-zinc-400">
        ← {date.market.name}
      </Link>
      <h1 className="mt-4 text-2xl font-semibold tracking-tight">{date.market.name}</h1>
      <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
        {formatOccurrence(date)} · {date.market.venue_name}, {[date.market.city, date.market.region].filter(Boolean).join(", ")} ·
        Organized by {date.market.organizer.name}
      </p>
      {date.status === "CANCELLED" && (
        <div className="mt-4 flex flex-wrap items-center gap-2 text-sm">
          <Badge tone="red">Cancelled</Badge>
          {date.cancellation_message}
        </div>
      )}

      {intake.ok && date.status === "SCHEDULED" && intake.data.state !== "not_accepting" && intake.data.state !== "closed" && (
        <Card className="mt-6 !p-4">
          <p className="text-sm">{intakeMessage(intake.data.state, intake.data.opens_at, date.timezone)}</p>
          {intake.data.state === "open" && (
            <Link href={`/markets/${marketId}/dates/${occurrenceId}/apply`} className="mt-2 inline-block text-sm font-medium underline">
              Apply as a vendor
            </Link>
          )}
        </Card>
      )}

      <section className="mt-8" aria-labelledby="layout-heading">
        <h2 id="layout-heading" className="text-lg font-semibold">
          Stall layout
        </h2>
        {layout.ok ? (
          <PublicLayoutView layout={layout.data} />
        ) : (
          <p className="mt-2 text-sm text-zinc-600 dark:text-zinc-400">
            {layout.status === 404
              ? "The organizer hasn't published a stall layout for this date."
              : "We couldn't load the stall layout right now."}
          </p>
        )}
      </section>
    </main>
  );
}
