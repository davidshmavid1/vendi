import { formatMinor } from "@/lib/layouts/money";

type Props = { stall: number; fee: number; total: number; currency: string; exponent: number };

/** Stall price, Vendi's fee (added on top) and the total charged. */
export function FeeBreakdown({ stall, fee, total, currency, exponent }: Props) {
  const money = (minor: number) => formatMinor(minor, currency, exponent);
  return (
    <dl className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-0.5 text-sm">
      <dt className="text-zinc-600 dark:text-zinc-400">Stall</dt>
      <dd className="text-right">{money(stall)}</dd>
      <dt className="text-zinc-600 dark:text-zinc-400">Vendi service fee</dt>
      <dd className="text-right">{money(fee)}</dd>
      <dt className="font-medium">Total</dt>
      <dd className="text-right font-medium">{money(total)}</dd>
    </dl>
  );
}
