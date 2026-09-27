import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Badge, Card } from "@/components/ui";
import { getPublic } from "@/lib/djangoApi";
import { formatOccurrence, MARKET_TYPES, type DiscoveryOccurrence, type MarketType } from "@/lib/discovery/logic";

type PublicMarket = {
  id: number;
  name: string;
  description: string;
  market_type: MarketType;
  venue_name: string;
  address_line1: string;
  address_line2: string;
  city: string;
  region: string;
  postal_code: string;
  country: string;
  timezone: string;
  organizer: { name: string };
};

type PublicOccurrence = DiscoveryOccurrence & {
  status: "SCHEDULED" | "CANCELLED";
  cancellation_message: string;
};

async function loadMarket(marketId: string) {
  if (!/^\d+$/.test(marketId)) notFound();
  const market = await getPublic<PublicMarket>(`/markets/${marketId}`);
  if (!market.ok && market.status === 404) notFound();
  return market;
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ marketId: string }>;
}): Promise<Metadata> {
  const market = await loadMarket((await params).marketId);
  return { title: market.ok ? `${market.data.name} · Vendi` : "Market · Vendi" };
}

export default async function MarketDetailPage({ params }: { params: Promise<{ marketId: string }> }) {
  const { marketId } = await params;
  const market = await loadMarket(marketId);

  if (!market.ok) {
    return (
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-10 sm:px-6">
        <Link href="/markets" className="text-sm text-zinc-600 underline dark:text-zinc-400">
          ← All markets
        </Link>
        <p className="mt-6 text-sm">
          {market.status === 503
            ? "Market details aren't available here yet."
            : "We couldn't load this market right now. Please try again."}
        </p>
      </main>
    );
  }

  const m = market.data;
  const dates = await getPublic<{ items: PublicOccurrence[] }>(`/markets/${marketId}/occurrences?limit=20`);
  const address = [m.address_line1, m.address_line2, [m.city, m.region].filter(Boolean).join(", "), m.postal_code, m.country]
    .filter(Boolean)
    .join(" · ");

  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-10 sm:px-6">
      <Link href="/markets" className="text-sm text-zinc-600 underline dark:text-zinc-400">
        ← All markets
      </Link>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">{m.name}</h1>
        <Badge tone="green">{MARKET_TYPES[m.market_type]}</Badge>
      </div>
      <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">Organized by {m.organizer.name}</p>

      <Card className="mt-6">
        <h2 className="text-sm font-semibold">Where</h2>
        <p className="mt-1">{m.venue_name}</p>
        <p className="text-sm text-zinc-600 dark:text-zinc-400">{address}</p>
        {m.description && <p className="mt-4 whitespace-pre-line text-sm">{m.description}</p>}
      </Card>

      <section className="mt-6" aria-labelledby="dates-heading">
        <h2 id="dates-heading" className="text-lg font-semibold">
          Upcoming dates
        </h2>
        <p className="text-xs text-zinc-500">Times are shown in the market&apos;s local time ({m.timezone}).</p>
        {!dates.ok ? (
          <p className="mt-3 text-sm">We couldn&apos;t load the dates right now.</p>
        ) : dates.data.items.length === 0 ? (
          <p className="mt-3 text-sm">No upcoming dates are scheduled.</p>
        ) : (
          <ul className="mt-3 flex flex-col gap-2">
            {dates.data.items.map((date) => (
              <li
                key={date.id}
                className="rounded-md border border-zinc-200 bg-white px-4 py-3 text-sm dark:border-zinc-800 dark:bg-zinc-900"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className={date.status === "CANCELLED" ? "line-through text-zinc-500" : ""}>
                    {formatOccurrence(date)}
                  </span>
                  {date.status === "CANCELLED" && <Badge tone="red">Cancelled</Badge>}
                </div>
                {date.status === "CANCELLED" && date.cancellation_message && (
                  <p className="mt-1 text-zinc-600 dark:text-zinc-400">{date.cancellation_message}</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <p className="mt-8 rounded-md border border-dashed border-zinc-300 p-4 text-sm text-zinc-600 dark:border-zinc-700 dark:text-zinc-400">
        Vendor applications for this market aren&apos;t available on Vendi yet.
      </p>
    </main>
  );
}
