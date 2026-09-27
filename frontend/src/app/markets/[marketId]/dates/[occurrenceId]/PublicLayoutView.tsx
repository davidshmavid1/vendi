"use client";

import { useState } from "react";
import { Badge, Card } from "@/components/ui";
import { formatMinor } from "@/lib/layouts/money";
import { physicalSize, type PublicLayout, type PublicStall } from "@/lib/layouts/types";

/** Read-only published layout: a visual plan plus an equivalent table. */
export function PublicLayoutView({ layout }: { layout: PublicLayout }) {
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const selected = layout.stalls.find((s) => s.id === selectedId) ?? null;
  const price = (s: PublicStall) => formatMinor(s.price_minor, layout.currency, layout.currency_exponent);
  const { canvas_width: w, canvas_height: h } = layout;
  const fontSize = Math.max(1, Math.min(w, h) / 25);

  return (
    <div className="mt-3 flex flex-col gap-4">
      <p className="text-sm text-zinc-600 dark:text-zinc-400">
        Prices are the organizer&apos;s listed prices for this date, in {layout.currency}; taxes or fees aren&apos;t included.
        Stalls shown here aren&apos;t reserved and may not all be available later.
      </p>
      <div className="grid gap-4 md:grid-cols-[1fr_18rem]">
        <Card className="!p-3">
          <svg viewBox={`0 0 ${w} ${h}`} className="h-auto w-full rounded bg-zinc-50 dark:bg-zinc-950" role="img" aria-label="Stall plan. The table below lists the same stalls.">
            <rect x={0} y={0} width={w} height={h} fill="none" stroke="currentColor" strokeOpacity={0.3} vectorEffect="non-scaling-stroke" />
            {layout.stalls.map((s) => {
              const isSelected = s.id === selectedId;
              return (
                <g key={s.id} className={s.offered ? "cursor-pointer" : "cursor-default"} onClick={() => setSelectedId(s.id)}>
                  <rect
                    x={s.x}
                    y={s.y}
                    width={s.width}
                    height={s.height}
                    className={
                      isSelected
                        ? "fill-orange-200 stroke-orange-600"
                        : s.offered
                          ? "fill-green-100 stroke-green-700"
                          : "fill-zinc-200 stroke-zinc-400"
                    }
                    strokeWidth={isSelected ? 3 : 1.5}
                    strokeDasharray={s.offered ? undefined : "4 3"}
                    vectorEffect="non-scaling-stroke"
                  />
                  <text
                    x={s.x + s.width / 2}
                    y={s.y + s.height / 2}
                    fontSize={Math.min(fontSize, s.height / 2, s.width / Math.max(2, s.label.length * 0.6))}
                    textAnchor="middle"
                    dominantBaseline="central"
                    className={`pointer-events-none ${s.offered ? "fill-zinc-800" : "fill-zinc-500"}`}
                  >
                    {s.label}
                  </text>
                </g>
              );
            })}
          </svg>
          <p className="mt-2 flex flex-wrap gap-4 text-xs text-zinc-500">
            <span>
              <span className="mr-1 inline-block h-3 w-3 border border-green-700 bg-green-100 align-middle" /> Offered
            </span>
            <span>
              <span className="mr-1 inline-block h-3 w-3 border border-dashed border-zinc-400 bg-zinc-200 align-middle" /> Not offered
            </span>
          </p>
        </Card>
        <Card className="!p-4" >
          <div aria-live="polite">
            {selected ? (
              <div className="flex flex-col gap-2 text-sm">
                <div className="flex items-center gap-2">
                  <h3 className="text-base font-semibold">Stall {selected.label}</h3>
                  {!selected.offered && <Badge>Not offered</Badge>}
                </div>
                {selected.offered && <p className="text-lg font-medium">{price(selected)}</p>}
                {physicalSize(selected) && <p>Size: {physicalSize(selected)}</p>}
                {selected.description && <p className="whitespace-pre-line text-zinc-700 dark:text-zinc-300">{selected.description}</p>}
                {!selected.offered && <p className="text-zinc-600 dark:text-zinc-400">The organizer isn&apos;t offering this stall.</p>}
              </div>
            ) : (
              <p className="text-sm text-zinc-500">Select a stall on the plan or in the table to see its details.</p>
            )}
          </div>
        </Card>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[28rem] text-left text-sm">
          <caption className="sr-only">Stalls for this date</caption>
          <thead className="text-xs text-zinc-500">
            <tr>
              <th scope="col" className="py-2 pr-4 font-medium">Stall</th>
              <th scope="col" className="py-2 pr-4 font-medium">Size</th>
              <th scope="col" className="py-2 pr-4 font-medium">Price</th>
              <th scope="col" className="py-2 font-medium">
                <span className="sr-only">Details</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {layout.stalls.map((s) => (
              <tr key={s.id} className={`border-t border-zinc-200 dark:border-zinc-800 ${s.id === selectedId ? "bg-orange-50 dark:bg-zinc-800" : ""}`}>
                <th scope="row" className="py-2 pr-4 font-medium">
                  {s.label}
                </th>
                <td className="py-2 pr-4">{physicalSize(s) ?? "—"}</td>
                <td className="py-2 pr-4">{s.offered ? price(s) : <span className="text-zinc-500">Not offered</span>}</td>
                <td className="py-2">
                  <button
                    type="button"
                    onClick={() => setSelectedId(s.id)}
                    aria-pressed={s.id === selectedId}
                    className="underline"
                    aria-label={`View stall details for ${s.label}`}
                  >
                    View stall details
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
