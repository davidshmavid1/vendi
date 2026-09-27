"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { Badge, Card } from "@/components/ui";
import { AccountGate, buttonClass, DjangoPage, inputClass, Notice, secondaryButtonClass } from "@/components/DjangoPage";
import { apiGet, apiSend, type ApiError } from "@/lib/django/client";
import { useAccount } from "@/lib/django/useAccount";
import type { MyOrganization } from "@/lib/applications/types";
import { clampPosition, fits, freeSpot, overlaps } from "@/lib/layouts/geometry";
import { CURRENCY_EXPONENTS, formatMinor, inputToMinor, minorToInput } from "@/lib/layouts/money";
import type { LayoutOut, PhysicalUnit } from "@/lib/layouts/types";

type DraftStall = {
  key: string;
  id: number | null;
  label: string;
  description: string;
  x: number;
  y: number;
  width: number;
  height: number;
  physicalWidth: string;
  physicalDepth: string;
  physicalUnit: PhysicalUnit | "";
  enabled: boolean;
  price: string; // major units as typed, e.g. "25.00"
};

type Draft = { canvasWidth: number; canvasHeight: number; currency: string; stalls: DraftStall[] };

const NEW_DRAFT: Draft = { canvasWidth: 100, canvasHeight: 60, currency: "USD", stalls: [] };
let keySeq = 0;
const newKey = () => `new-${++keySeq}`;

function toDraft(layout: LayoutOut): Draft {
  return {
    canvasWidth: layout.canvas_width,
    canvasHeight: layout.canvas_height,
    currency: layout.currency,
    stalls: layout.stalls.map((s) => ({
      key: `id-${s.id}`,
      id: s.id,
      label: s.label,
      description: s.description,
      x: s.x,
      y: s.y,
      width: s.width,
      height: s.height,
      physicalWidth: s.physical_width ?? "",
      physicalDepth: s.physical_depth ?? "",
      physicalUnit: s.physical_unit ?? "",
      enabled: s.enabled,
      price: minorToInput(s.price_minor, layout.currency_exponent),
    })),
  };
}

/** Problems the editor can see before saving, keyed by stall key ("" = layout). */
function draftProblems(draft: Draft): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  const add = (key: string, message: string) => (out[key] ??= []).push(message);
  const exponent = CURRENCY_EXPONENTS[draft.currency];
  if (exponent === undefined) add("", "Choose a supported currency.");
  for (const [field, value] of [
    ["Canvas width", draft.canvasWidth],
    ["Canvas height", draft.canvasHeight],
  ] as const) {
    if (!Number.isInteger(value) || value < 1 || value > 10000) add("", `${field} must be a whole number from 1 to 10000.`);
  }
  const labels = new Map<string, string>();
  draft.stalls.forEach((s, i) => {
    const label = s.label.trim();
    if (!label) add(s.key, "Label is required.");
    else if (labels.has(label.toLowerCase())) add(s.key, `Label "${label}" is used by another stall.`);
    else labels.set(label.toLowerCase(), s.key);
    if (![s.x, s.y, s.width, s.height].every(Number.isInteger) || s.width < 1 || s.height < 1) {
      add(s.key, "Position and size must be whole numbers; width and height at least 1.");
    } else if (!fits(s, draft.canvasWidth, draft.canvasHeight)) {
      add(s.key, "Must fit inside the canvas.");
    }
    if (exponent !== undefined && inputToMinor(s.price, exponent) === null) {
      add(s.key, exponent === 0 ? "Price must be a whole amount." : `Price must be an amount with up to ${exponent} decimals.`);
    }
    const physical = [s.physicalWidth.trim(), s.physicalDepth.trim(), s.physicalUnit];
    if (physical.some(Boolean) && !physical.every(Boolean)) add(s.key, "Give physical width, depth and unit together.");
    else if (physical.every(Boolean) && !(Number(s.physicalWidth) > 0 && Number(s.physicalDepth) > 0)) {
      add(s.key, "Physical width and depth must be positive numbers.");
    }
    for (let j = 0; j < i; j++) {
      if (overlaps(draft.stalls[j], s)) add(s.key, `Overlaps ${draft.stalls[j].label.trim() || "another stall"}.`);
    }
  });
  return out;
}

function payload(draft: Draft, revision: number | null) {
  const exponent = CURRENCY_EXPONENTS[draft.currency] ?? 2;
  return {
    expected_revision: revision,
    canvas_width: draft.canvasWidth,
    canvas_height: draft.canvasHeight,
    currency: draft.currency,
    stalls: draft.stalls.map((s) => ({
      ...(s.id !== null ? { id: s.id } : {}),
      label: s.label.trim(),
      description: s.description.trim(),
      x: s.x,
      y: s.y,
      width: s.width,
      height: s.height,
      physical_width: s.physicalWidth.trim() || null,
      physical_depth: s.physicalDepth.trim() || null,
      physical_unit: s.physicalUnit || null,
      enabled: s.enabled,
      price_minor: inputToMinor(s.price, exponent) ?? 0,
    })),
  };
}

type Props = { organizationId: number; marketId: number; occurrenceId: number };

export function LayoutEditor({ organizationId, marketId, occurrenceId }: Props) {
  const { state: account, reload } = useAccount();
  const base = `/organizations/${organizationId}/markets/${marketId}/occurrences/${occurrenceId}/layout`;
  const [role, setRole] = useState<MyOrganization["role"] | null>(null);
  const [saved, setSaved] = useState<LayoutOut | null>(null);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "missing" | "failed">("loading");
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [draft, setDraft] = useState<Draft>(NEW_DRAFT);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState<null | "save" | "publish" | "unpublish">(null);
  const [serverError, setServerError] = useState<ApiError | null>(null);
  const [serverProblems, setServerProblems] = useState<Record<string, string[]>>({});
  const [notice, setNotice] = useState<string | null>(null);
  const signedIn = account.status === "signed_in";

  const load = useCallback(async () => {
    setLoadState("loading");
    const [layout, membership] = await Promise.all([
      apiGet<LayoutOut>(base),
      apiGet<MyOrganization>(`/organizations/${organizationId}`),
    ]);
    if (membership.ok) setRole(membership.data.role);
    setServerError(null);
    setServerProblems({});
    if (layout.ok) {
      setSaved(layout.data);
      setDraft(toDraft(layout.data));
      setLoadState("ready");
    } else if (layout.error.code === "layout_not_found") {
      setSaved(null);
      setDraft(NEW_DRAFT);
      setLoadState("missing");
    } else {
      setLoadError(layout.error);
      setLoadState("failed");
    }
  }, [base, organizationId]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial load once signed in
    if (signedIn) void load();
  }, [signedIn, load]);

  const baseline = useMemo(() => JSON.stringify(saved ? toDraft(saved) : NEW_DRAFT), [saved]);
  const dirty = JSON.stringify(draft) !== baseline;
  const canEdit = role === "OWNER" || role === "ADMIN";
  const published = saved?.published ?? false;
  const editable = canEdit && !published;
  const problems = useMemo(() => draftProblems(draft), [draft]);
  const problemCount = Object.values(problems).flat().length;
  const exponent = CURRENCY_EXPONENTS[draft.currency] ?? 2;

  // Warn before leaving the page with unsaved edits.
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  function update(key: string, change: Partial<DraftStall>) {
    setDraft((d) => ({ ...d, stalls: d.stalls.map((s) => (s.key === key ? { ...s, ...change } : s)) }));
    setNotice(null);
  }

  function addStall() {
    const size = Math.max(1, Math.round(Math.min(draft.canvasWidth, draft.canvasHeight) / 6));
    const spot = freeSpot(draft.stalls, size, draft.canvasWidth, draft.canvasHeight);
    if (!spot) {
      setNotice("There's no free space for another stall. Enlarge the canvas or resize stalls.");
      return;
    }
    const used = new Set(draft.stalls.map((s) => s.label.trim().toLowerCase()));
    let n = draft.stalls.length + 1;
    while (used.has(`s${n}`)) n++;
    const stall: DraftStall = {
      key: newKey(),
      id: null,
      label: `S${n}`,
      description: "",
      ...spot,
      width: size,
      height: size,
      physicalWidth: "",
      physicalDepth: "",
      physicalUnit: "",
      enabled: true,
      price: minorToInput(0, exponent),
    };
    setDraft((d) => ({ ...d, stalls: [...d.stalls, stall] }));
    setSelected(stall.key);
  }

  async function save() {
    if (problemCount > 0) return;
    setBusy("save");
    setServerError(null);
    setServerProblems({});
    const sent = draft;
    const result = await apiSend<LayoutOut>("PUT", base, payload(sent, saved?.revision ?? null));
    setBusy(null);
    if (result.ok) {
      setSaved(result.data);
      setDraft(toDraft(result.data));
      setLoadState("ready");
      setSelected((key) => {
        // New stalls get ids on save: keep the same stall selected.
        const index = sent.stalls.findIndex((s) => s.key === key);
        return index >= 0 ? `id-${result.data.stalls[index].id}` : null;
      });
      setNotice("Layout saved.");
      return;
    }
    setServerError(result.error);
    if (result.error.code === "layout_invalid" || result.error.code === "stall_mismatch") {
      const mapped: Record<string, string[]> = {};
      for (const detail of result.error.details ?? []) {
        const index = typeof detail.index === "number" ? detail.index : -1;
        const key = index >= 0 ? sent.stalls[index]?.key ?? "" : "";
        (mapped[key] ??= []).push(String(detail.message));
      }
      setServerProblems(mapped);
    }
  }

  async function setPublished(publish: boolean) {
    if (!saved) return;
    const question = publish
      ? "Publish this layout? Vendors will be able to see the stalls and prices."
      : "Unpublish this layout? Vendors will no longer see it until you publish again.";
    if (!window.confirm(question)) return;
    setBusy(publish ? "publish" : "unpublish");
    setServerError(null);
    const result = await apiSend<LayoutOut>(
      "POST",
      `${base}/${publish ? "publish" : "unpublish"}`,
      publish ? { expected_revision: saved.revision } : {},
    );
    setBusy(null);
    if (result.ok) {
      setSaved(result.data);
      setDraft(toDraft(result.data));
      setNotice(publish ? "Layout published." : "Layout unpublished. You can edit it now.");
    } else setServerError(result.error);
  }

  function discard() {
    if (!window.confirm("Discard your unsaved changes?")) return;
    setDraft(saved ? toDraft(saved) : NEW_DRAFT);
    setServerError(null);
    setServerProblems({});
  }

  async function reloadLatest() {
    if (dirty && !window.confirm("Load the latest saved layout? Your unsaved changes will be lost.")) return;
    await load();
  }

  const selectedStall = draft.stalls.find((s) => s.key === selected) ?? null;
  const back = `/organizer/${organizationId}/markets/${marketId}`;

  return (
    <DjangoPage account={account} wide>
      <Link
        href={back}
        className="text-sm text-zinc-600 underline dark:text-zinc-400"
        onClick={(e) => {
          if (dirty && !window.confirm("Leave without saving your changes?")) e.preventDefault();
        }}
      >
        ← Dates
      </Link>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">Stall layout</h1>
        {loadState === "ready" && (
          <Badge tone={published ? "green" : "zinc"}>{published ? "Published: vendors can see it" : "Not published"}</Badge>
        )}
        {dirty && <Badge tone="yellow">Unsaved changes</Badge>}
      </div>
      <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
        This date&apos;s own plan and prices. Other dates aren&apos;t affected. Showing stalls here doesn&apos;t reserve or sell them.
      </p>

      <div className="mt-6">
        <AccountGate account={account} retry={reload}>
          {loadState === "loading" ? (
            <p className="text-sm text-zinc-500">Loading…</p>
          ) : loadState === "failed" ? (
            <Notice tone="red">
              {loadError?.code === "not_found" ? "Event date not found." : loadError?.message}{" "}
              <button type="button" className="underline" onClick={load}>
                Try again
              </button>
            </Notice>
          ) : (
            <div className="flex flex-col gap-4">
              {!canEdit && role && <Notice>You can view this layout. Owners and admins edit and publish it.</Notice>}
              {canEdit && published && (
                <Notice tone="amber">This layout is published. Unpublish it to make changes.</Notice>
              )}
              {loadState === "missing" && !dirty && canEdit && (
                <Notice>This date has no layout yet. Set the canvas and add stalls, then save.</Notice>
              )}
              {serverError && (
                <Notice tone="red">
                  {serverError.message}{" "}
                  {serverError.code === "stale_revision" && (
                    <button type="button" className="underline" onClick={reloadLatest}>
                      Load the latest version
                    </button>
                  )}
                </Notice>
              )}
              {notice && <Notice tone="green">{notice}</Notice>}

              <Card>
                <div className="grid gap-4 sm:grid-cols-3">
                  <NumberField
                    label="Canvas width (units)"
                    value={draft.canvasWidth}
                    disabled={!editable}
                    onChange={(v) => setDraft((d) => ({ ...d, canvasWidth: v }))}
                  />
                  <NumberField
                    label="Canvas height (units)"
                    value={draft.canvasHeight}
                    disabled={!editable}
                    onChange={(v) => setDraft((d) => ({ ...d, canvasHeight: v }))}
                  />
                  <label className="flex flex-col gap-1 text-sm">
                    <span className="font-medium">Currency</span>
                    <select
                      value={draft.currency}
                      disabled={!editable}
                      onChange={(e) => setDraft((d) => ({ ...d, currency: e.target.value }))}
                      className={inputClass}
                    >
                      {Object.keys(CURRENCY_EXPONENTS).map((c) => (
                        <option key={c}>{c}</option>
                      ))}
                    </select>
                  </label>
                </div>
                <p className="mt-2 text-xs text-zinc-500">
                  Canvas units are a drawing grid, not real-world measurements. Set each stall&apos;s real size below.
                </p>
                {problems[""]?.map((p) => (
                  <p key={p} className="mt-2 text-sm text-red-600">
                    {p}
                  </p>
                ))}
              </Card>

              <div className="grid gap-4 lg:grid-cols-[1fr_22rem]">
                <Card className="!p-3">
                  <Canvas
                    draft={draft}
                    selected={selected}
                    invalid={new Set([...Object.keys(problems), ...Object.keys(serverProblems)])}
                    editable={editable}
                    onSelect={setSelected}
                    onMove={(key, x, y) => update(key, { x, y })}
                  />
                  {editable && (
                    <p className="mt-2 text-xs text-zinc-500">
                      Drag a stall to move it, or select it and use the arrow keys (Shift for 10 units). All values can also be
                      typed in the stall panel.
                    </p>
                  )}
                </Card>
                <div className="flex flex-col gap-4">
                  {editable && (
                    <button type="button" onClick={addStall} className={secondaryButtonClass}>
                      Add stall
                    </button>
                  )}
                  {selectedStall ? (
                    <StallPanel
                      stall={selectedStall}
                      editable={editable}
                      currency={draft.currency}
                      problems={[...(problems[selectedStall.key] ?? []), ...(serverProblems[selectedStall.key] ?? [])]}
                      onChange={(change) => update(selectedStall.key, change)}
                    />
                  ) : (
                    <p className="text-sm text-zinc-500">Select a stall to see and edit its details.</p>
                  )}
                </div>
              </div>

              <Card>
                <h2 className="text-sm font-semibold">Stalls ({draft.stalls.length})</h2>
                {draft.stalls.length === 0 ? (
                  <p className="mt-2 text-sm text-zinc-500">No stalls yet.</p>
                ) : (
                  <div className="mt-2 overflow-x-auto">
                    <table className="w-full min-w-[30rem] text-left text-sm">
                      <thead className="text-xs text-zinc-500">
                        <tr>
                          <th className="py-1 pr-3 font-medium">Label</th>
                          <th className="py-1 pr-3 font-medium">Position</th>
                          <th className="py-1 pr-3 font-medium">Size</th>
                          <th className="py-1 pr-3 font-medium">Price</th>
                          <th className="py-1 font-medium">Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {draft.stalls.map((s) => {
                          const issues = [...(problems[s.key] ?? []), ...(serverProblems[s.key] ?? [])];
                          const minor = inputToMinor(s.price, exponent);
                          return (
                            <tr key={s.key} className="border-t border-zinc-200 dark:border-zinc-800">
                              <td className="py-1 pr-3">
                                <button type="button" onClick={() => setSelected(s.key)} className="underline">
                                  {s.label || "(no label)"}
                                </button>
                                {issues.length > 0 && <span className="ml-2 text-xs text-red-600">{issues.join(" ")}</span>}
                              </td>
                              <td className="py-1 pr-3">
                                {s.x}, {s.y}
                              </td>
                              <td className="py-1 pr-3">
                                {s.width} × {s.height}
                              </td>
                              <td className="py-1 pr-3">{minor === null ? "—" : formatMinor(minor, draft.currency, exponent)}</td>
                              <td className="py-1">{s.enabled ? "Offered" : "Disabled"}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>

              {canEdit && (
                <div className="flex flex-wrap items-center gap-3">
                  {!published && (
                    <>
                      <button type="button" onClick={save} disabled={!dirty || problemCount > 0 || busy !== null} className={buttonClass}>
                        {busy === "save" ? "Saving…" : "Save layout"}
                      </button>
                      {dirty && (
                        <button type="button" onClick={discard} disabled={busy !== null} className={secondaryButtonClass}>
                          Discard changes
                        </button>
                      )}
                      {saved && (
                        <button
                          type="button"
                          onClick={() => setPublished(true)}
                          disabled={dirty || busy !== null}
                          className={secondaryButtonClass}
                        >
                          {busy === "publish" ? "Publishing…" : "Publish"}
                        </button>
                      )}
                    </>
                  )}
                  {published && (
                    <button type="button" onClick={() => setPublished(false)} disabled={busy !== null} className={secondaryButtonClass}>
                      {busy === "unpublish" ? "Unpublishing…" : "Unpublish to edit"}
                    </button>
                  )}
                  {problemCount > 0 && <span className="text-sm text-red-600">Fix {problemCount} problem(s) before saving.</span>}
                  {dirty && saved && !published && <span className="text-xs text-zinc-500">Save before publishing.</span>}
                </div>
              )}
            </div>
          )}
        </AccountGate>
      </div>
    </DjangoPage>
  );
}

function NumberField(props: { label: string; value: number; disabled?: boolean; min?: number; onChange: (v: number) => void }) {
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

function StallPanel({
  stall,
  editable,
  currency,
  problems,
  onChange,
}: {
  stall: DraftStall;
  editable: boolean;
  currency: string;
  problems: string[];
  onChange: (change: Partial<DraftStall>) => void;
}) {
  const exponent = CURRENCY_EXPONENTS[currency] ?? 2;
  return (
    <Card className="!p-4">
      <h2 className="text-sm font-semibold">Stall {stall.label || ""}</h2>
      {problems.length > 0 && (
        <ul role="alert" className="mt-2 list-disc pl-5 text-xs text-red-600">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      <fieldset disabled={!editable} className="mt-3 flex min-w-0 flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">Label</span>
          <input value={stall.label} maxLength={40} onChange={(e) => onChange({ label: e.target.value })} className={inputClass} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">Description (optional)</span>
          <textarea
            value={stall.description}
            maxLength={500}
            rows={2}
            onChange={(e) => onChange({ description: e.target.value })}
            className={inputClass}
          />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <NumberField label="X" value={stall.x} onChange={(x) => onChange({ x })} />
          <NumberField label="Y" value={stall.y} onChange={(y) => onChange({ y })} />
          <NumberField label="Width" min={1} value={stall.width} onChange={(width) => onChange({ width })} />
          <NumberField label="Height" min={1} value={stall.height} onChange={(height) => onChange({ height })} />
        </div>
        <div>
          <span className="text-sm font-medium">Real size (optional)</span>
          <div className="mt-1 grid grid-cols-[1fr_1fr_5rem] gap-2">
            <input
              aria-label="Real width"
              inputMode="decimal"
              placeholder="Width"
              value={stall.physicalWidth}
              onChange={(e) => onChange({ physicalWidth: e.target.value })}
              className={inputClass}
            />
            <input
              aria-label="Real depth"
              inputMode="decimal"
              placeholder="Depth"
              value={stall.physicalDepth}
              onChange={(e) => onChange({ physicalDepth: e.target.value })}
              className={inputClass}
            />
            <select
              aria-label="Unit"
              value={stall.physicalUnit}
              onChange={(e) => onChange({ physicalUnit: e.target.value as PhysicalUnit | "" })}
              className={inputClass}
            >
              <option value="">Unit</option>
              <option value="FT">ft</option>
              <option value="M">m</option>
            </select>
          </div>
        </div>
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">Price ({currency})</span>
          <input
            inputMode="decimal"
            value={stall.price}
            onChange={(e) => onChange({ price: e.target.value })}
            className={inputClass}
            aria-describedby="price-help"
          />
          <span id="price-help" className="text-xs text-zinc-500">
            {exponent === 0 ? "Whole amounts only." : `Up to ${exponent} decimal places.`} Taxes and fees aren&apos;t added.
          </span>
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={stall.enabled} onChange={(e) => onChange({ enabled: e.target.checked })} />
          Offered to vendors (uncheck to disable this stall)
        </label>
      </fieldset>
    </Card>
  );
}

function Canvas({
  draft,
  selected,
  invalid,
  editable,
  onSelect,
  onMove,
}: {
  draft: Draft;
  selected: string | null;
  invalid: Set<string>;
  editable: boolean;
  onSelect: (key: string) => void;
  onMove: (key: string, x: number, y: number) => void;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const drag = useRef<{ key: string; dx: number; dy: number } | null>(null);
  const { canvasWidth: w, canvasHeight: h } = draft;
  const valid = w >= 1 && h >= 1 && w <= 10000 && h <= 10000;
  const fontSize = Math.max(1, Math.min(w, h) / 25);

  function toCanvas(event: PointerEvent) {
    const svg = svgRef.current;
    const matrix = svg?.getScreenCTM();
    if (!svg || !matrix) return null;
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
    return { x: point.x, y: point.y };
  }

  function onPointerDown(event: PointerEvent<SVGGElement>, stall: DraftStall) {
    onSelect(stall.key);
    if (!editable) return;
    const point = toCanvas(event);
    if (!point) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { key: stall.key, dx: point.x - stall.x, dy: point.y - stall.y };
  }

  function onPointerMove(event: PointerEvent<SVGGElement>, stall: DraftStall) {
    if (drag.current?.key !== stall.key) return;
    const point = toCanvas(event);
    if (!point) return;
    const next = clampPosition({ ...stall, x: point.x - drag.current.dx, y: point.y - drag.current.dy }, w, h);
    if (next.x !== stall.x || next.y !== stall.y) onMove(stall.key, next.x, next.y);
  }

  function onKeyDown(event: KeyboardEvent<SVGGElement>, stall: DraftStall) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(stall.key);
      return;
    }
    const step = event.shiftKey ? 10 : 1;
    const delta = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[event.key];
    if (!delta || !editable) return;
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
      className="h-auto w-full touch-none select-none rounded bg-zinc-50 dark:bg-zinc-950"
      role="group"
      aria-label={`Layout plan, ${w} by ${h} units`}
    >
      <rect x={0} y={0} width={w} height={h} fill="none" stroke="currentColor" strokeOpacity={0.3} vectorEffect="non-scaling-stroke" />
      {draft.stalls.map((s) => {
        const isSelected = s.key === selected;
        const bad = invalid.has(s.key);
        return (
          <g
            key={s.key}
            role="button"
            tabIndex={0}
            aria-pressed={isSelected}
            aria-label={`Stall ${s.label}, at ${s.x}, ${s.y}, ${s.width} by ${s.height}${s.enabled ? "" : ", disabled"}${
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
              x={s.x}
              y={s.y}
              width={Math.max(0, s.width)}
              height={Math.max(0, s.height)}
              className={
                bad
                  ? "fill-red-200 stroke-red-600"
                  : isSelected
                    ? "fill-orange-200 stroke-orange-600"
                    : s.enabled
                      ? "fill-green-100 stroke-green-700"
                      : "fill-zinc-200 stroke-zinc-400"
              }
              strokeWidth={isSelected ? 3 : 1.5}
              strokeDasharray={s.enabled ? undefined : "4 3"}
              vectorEffect="non-scaling-stroke"
            />
            <text
              x={s.x + s.width / 2}
              y={s.y + s.height / 2}
              fontSize={Math.min(fontSize, s.height / 2, s.width / Math.max(2, s.label.length * 0.6))}
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
