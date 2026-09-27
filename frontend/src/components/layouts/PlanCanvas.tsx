"use client";

import { useRef, type KeyboardEvent, type PointerEvent } from "react";
import { inputClass } from "@/components/DjangoPage";
import { clampPosition } from "@/lib/layouts/geometry";

/** A stall as drawn on the plan. ``muted``: shown dashed and grey
 *  (e.g. not offered on a date). */
export type PlanStall = {
  key: string;
  label: string;
  x: number;
  y: number;
  width: number;
  height: number;
  muted?: boolean;
};

/** Blank or partly typed number fields are NaN while editing: draw them as 0
 *  (the editor's validation still reports them and blocks saving). */
const finite = (value: number) => (Number.isFinite(value) ? value : 0);

/** Only move a stall whose position and size are real numbers; otherwise a
 *  drag or arrow key would turn a valid X/Y into NaN. */
const movable = (s: PlanStall) => [s.x, s.y, s.width, s.height].every(Number.isFinite);

export function NumberField(props: { label: string; value: number; disabled?: boolean; min?: number; onChange: (v: number) => void }) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="font-medium">{props.label}</span>
      <input
        type="number"
        inputMode="numeric"
        step={1}
        min={props.min ?? 0}
        value={Number.isFinite(props.value) ? props.value : ""}
        disabled={props.disabled}
        onChange={(e) => props.onChange(e.target.value === "" ? NaN : Number(e.target.value))}
        className={inputClass}
      />
    </label>
  );
}

export function PlanCanvas({
  stalls,
  canvasWidth: w,
  canvasHeight: h,
  selected,
  invalid,
  editable,
  onSelect,
  onMove,
}: {
  stalls: PlanStall[];
  canvasWidth: number;
  canvasHeight: number;
  selected: string | null;
  invalid: Set<string>;
  editable: boolean;
  onSelect: (key: string) => void;
  onMove: (key: string, x: number, y: number) => void;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const drag = useRef<{ key: string; dx: number; dy: number } | null>(null);
  const valid = w >= 1 && h >= 1 && w <= 10000 && h <= 10000;
  const fontSize = Math.max(1, Math.min(w, h) / 25);

  function toCanvas(event: PointerEvent) {
    const svg = svgRef.current;
    const matrix = svg?.getScreenCTM();
    if (!svg || !matrix) return null;
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
    return { x: point.x, y: point.y };
  }

  function onPointerDown(event: PointerEvent<SVGGElement>, stall: PlanStall) {
    onSelect(stall.key);
    if (!editable || !movable(stall)) return;
    const point = toCanvas(event);
    if (!point) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { key: stall.key, dx: point.x - stall.x, dy: point.y - stall.y };
  }

  function onPointerMove(event: PointerEvent<SVGGElement>, stall: PlanStall) {
    if (drag.current?.key !== stall.key) return;
    const point = toCanvas(event);
    if (!point) return;
    const next = clampPosition({ ...stall, x: point.x - drag.current.dx, y: point.y - drag.current.dy }, w, h);
    if (next.x !== stall.x || next.y !== stall.y) onMove(stall.key, next.x, next.y);
  }

  function onKeyDown(event: KeyboardEvent<SVGGElement>, stall: PlanStall) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(stall.key);
      return;
    }
    const step = event.shiftKey ? 10 : 1;
    const delta = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[event.key];
    if (!delta || !editable || !movable(stall)) return;
    event.preventDefault();
    onSelect(stall.key);
    const next = clampPosition({ ...stall, x: stall.x + delta[0], y: stall.y + delta[1] }, w, h);
    onMove(stall.key, next.x, next.y);
  }

  if (!valid) return <p className="p-4 text-sm text-zinc-500">Set a valid canvas size to see the plan.</p>;

  return (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${w} ${h}`}
      className="h-auto max-h-[60vh] w-full touch-none select-none rounded bg-zinc-50 dark:bg-zinc-950"
      role="group"
      aria-label={`Layout plan, ${w} by ${h} units`}
    >
      <rect x={0} y={0} width={w} height={h} fill="none" stroke="currentColor" strokeOpacity={0.3} vectorEffect="non-scaling-stroke" />
      {stalls.map((s) => {
        const isSelected = s.key === selected;
        const bad = invalid.has(s.key);
        const x = finite(s.x);
        const y = finite(s.y);
        const width = Math.max(0, finite(s.width));
        const height = Math.max(0, finite(s.height));
        return (
          <g
            key={s.key}
            role="button"
            tabIndex={0}
            aria-pressed={isSelected}
            aria-label={`Stall ${s.label}, at ${s.x}, ${s.y}, ${s.width} by ${s.height}${s.muted ? ", not offered" : ""}${
              editable ? ". Arrow keys move it." : ""
            }`}
            className={`${editable ? "cursor-move" : "cursor-pointer"} outline-none focus-visible:[&>rect]:stroke-blue-600`}
            onPointerDown={(e) => onPointerDown(e, s)}
            onPointerMove={(e) => onPointerMove(e, s)}
            onPointerUp={() => (drag.current = null)}
            onPointerCancel={() => (drag.current = null)}
            onKeyDown={(e) => onKeyDown(e, s)}
          >
            <rect
              x={x}
              y={y}
              width={width}
              height={height}
              className={
                bad
                  ? "fill-red-200 stroke-red-600"
                  : isSelected
                    ? "fill-orange-200 stroke-orange-600"
                    : s.muted
                      ? "fill-zinc-200 stroke-zinc-400"
                      : "fill-green-100 stroke-green-700"
              }
              strokeWidth={isSelected ? 3 : 1.5}
              strokeDasharray={s.muted ? "4 3" : undefined}
              vectorEffect="non-scaling-stroke"
            />
            <text
              x={x + width / 2}
              y={y + height / 2}
              fontSize={Math.min(fontSize, height / 2, width / Math.max(2, s.label.length * 0.6))}
              textAnchor="middle"
              dominantBaseline="central"
              className="pointer-events-none fill-zinc-800"
            >
              {s.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
