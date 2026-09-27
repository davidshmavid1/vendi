"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge, Card } from "@/components/ui";
import { AccountGate, buttonClass, DjangoPage, inputClass, Notice, secondaryButtonClass } from "@/components/DjangoPage";
import { NumberField, PlanCanvas } from "@/components/layouts/PlanCanvas";
import { apiGet, apiSend, type ApiError } from "@/lib/django/client";
import { useAccount } from "@/lib/django/useAccount";
import type { MyOrganization } from "@/lib/applications/types";
import { fits, freeSpot, overlaps } from "@/lib/layouts/geometry";
import { physicalSize, type PhysicalUnit, type VersionOut } from "@/lib/layouts/types";

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
};

type Draft = { canvasWidth: number; canvasHeight: number; stalls: DraftStall[] };

let keySeq = 0;
const newKey = () => `new-${++keySeq}`;

function toDraft(version: VersionOut): Draft {
  return {
    canvasWidth: version.canvas_width,
    canvasHeight: version.canvas_height,
    stalls: version.stalls.map((s) => ({
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
    })),
  };
}

/** Problems visible before saving, keyed by stall key ("" = canvas). */
function draftProblems(draft: Draft): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  const add = (key: string, message: string) => (out[key] ??= []).push(message);
  for (const [field, value] of [
    ["Canvas width", draft.canvasWidth],
    ["Canvas height", draft.canvasHeight],
  ] as const) {
    if (!Number.isInteger(value) || value < 1 || value > 10000) add("", `${field} must be a whole number from 1 to 10000.`);
  }
  const labels = new Set<string>();
  draft.stalls.forEach((s, i) => {
    const label = s.label.trim();
    if (!label) add(s.key, "Label is required.");
    else if (labels.has(label.toLowerCase())) add(s.key, `Label "${label}" is used by another stall.`);
    else labels.add(label.toLowerCase());
    if (![s.x, s.y, s.width, s.height].every(Number.isInteger) || s.width < 1 || s.height < 1) {
      add(s.key, "Position and size must be whole numbers; width and height at least 1.");
    } else if (!fits(s, draft.canvasWidth, draft.canvasHeight)) {
      add(s.key, "Must fit inside the canvas.");
    }
    const physical = [s.physicalWidth.trim(), s.physicalDepth.trim(), s.physicalUnit];
    if (physical.some(Boolean) && !physical.every(Boolean)) add(s.key, "Give real width, depth and unit together.");
    else if (physical.every(Boolean) && !(Number(s.physicalWidth) > 0 && Number(s.physicalDepth) > 0)) {
      add(s.key, "Real width and depth must be positive numbers.");
    }
    for (let j = 0; j < i; j++) {
      if (overlaps(draft.stalls[j], s)) add(s.key, `Overlaps ${draft.stalls[j].label.trim() || "another stall"}.`);
    }
  });
  return out;
}

function payload(draft: Draft, revision: number) {
  return {
    expected_revision: revision,
    canvas_width: draft.canvasWidth,
    canvas_height: draft.canvasHeight,
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
    })),
  };
}

type Props = { organizationId: number; marketId: number; versionId: number };

/** Edits one layout version's plan. Locked versions (used by a date) are read-only. */
export function VersionEditor({ organizationId, marketId, versionId }: Props) {
  const router = useRouter();
  const { state: account, reload } = useAccount();
  const marketBase = `/organizations/${organizationId}/markets/${marketId}`;
  const base = `${marketBase}/layout-versions/${versionId}`;
  const back = `/organizer/${organizationId}/markets/${marketId}`;
  const [role, setRole] = useState<MyOrganization["role"] | null>(null);
  const [saved, setSaved] = useState<VersionOut | null>(null);
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState<null | "save" | "copy">(null);
  const [serverError, setServerError] = useState<ApiError | null>(null);
  const [serverProblems, setServerProblems] = useState<Record<string, string[]>>({});
  const [notice, setNotice] = useState<string | null>(null);
  const signedIn = account.status === "signed_in";

  const load = useCallback(async () => {
    const [version, membership] = await Promise.all([
      apiGet<VersionOut>(base),
      apiGet<MyOrganization>(`/organizations/${organizationId}`),
    ]);
    if (membership.ok) setRole(membership.data.role);
    setServerError(null);
    setServerProblems({});
    if (version.ok) {
      setSaved(version.data);
      setDraft(toDraft(version.data));
      setLoadError(null);
    } else setLoadError(version.error);
  }, [base, organizationId]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial load once signed in
    if (signedIn) void load();
  }, [signedIn, load]);

  const baseline = useMemo(() => (saved ? JSON.stringify(toDraft(saved)) : ""), [saved]);
  const dirty = draft !== null && JSON.stringify(draft) !== baseline;
  const canEdit = role === "OWNER" || role === "ADMIN";
  const editable = canEdit && saved !== null && !saved.locked;
  const problems = useMemo(() => (draft ? draftProblems(draft) : {}), [draft]);
  const problemCount = Object.values(problems).flat().length;

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  function update(key: string, change: Partial<DraftStall>) {
    setDraft((d) => d && { ...d, stalls: d.stalls.map((s) => (s.key === key ? { ...s, ...change } : s)) });
    setNotice(null);
  }

  function addStall() {
    if (!draft) return;
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
    };
    setDraft({ ...draft, stalls: [...draft.stalls, stall] });
    setSelected(stall.key);
  }

  async function save() {
    if (!draft || !saved || problemCount > 0) return;
    setBusy("save");
    setServerError(null);
    setServerProblems({});
    const sent = draft;
    const result = await apiSend<VersionOut>("PUT", base, payload(sent, saved.revision));
    setBusy(null);
    if (result.ok) {
      setSaved(result.data);
      setDraft(toDraft(result.data));
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
        const key = index >= 0 ? (sent.stalls[index]?.key ?? "") : "";
        (mapped[key] ??= []).push(String(detail.message));
      }
      setServerProblems(mapped);
    }
  }

  async function copy() {
    if (dirty && !window.confirm("Make a copy of the saved layout? Your unsaved changes won't be included.")) return;
    setBusy("copy");
    const result = await apiSend<VersionOut>("POST", `${marketBase}/layout-versions`, { copy_of: versionId });
    setBusy(null);
    if (result.ok) router.push(`${back}/layouts/${result.data.id}`);
    else setServerError(result.error);
  }

  function discard() {
    if (!saved || !window.confirm("Discard your unsaved changes?")) return;
    setDraft(toDraft(saved));
    setServerError(null);
    setServerProblems({});
  }

  async function reloadLatest() {
    if (dirty && !window.confirm("Load the latest saved layout? Your unsaved changes will be lost.")) return;
    await load();
  }

  const selectedStall = draft?.stalls.find((s) => s.key === selected) ?? null;

  return (
    <DjangoPage account={account} wide>
      <Link
        href={back}
        className="text-sm text-zinc-600 underline dark:text-zinc-400"
        onClick={(e) => {
          if (dirty && !window.confirm("Leave without saving your changes?")) e.preventDefault();
        }}
      >
        ← Market layouts and dates
      </Link>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">Stall layout{saved ? ` v${saved.number}` : ""}</h1>
        {saved && (
          <Badge tone={saved.locked ? "blue" : "zinc"}>{saved.locked ? "In use by event dates · read-only" : "Draft · editable"}</Badge>
        )}
        {dirty && <Badge tone="yellow">Unsaved changes</Badge>}
      </div>
      <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
        The physical plan: stall positions, sizes and labels. Prices are set per date. Once a date uses this layout it
        can&apos;t change; make a copy to plan changes.
      </p>

      <div className="mt-6">
        <AccountGate account={account} retry={reload}>
          {loadError ? (
            <Notice tone="red">{loadError.code === "layout_version_not_found" ? "Layout not found." : loadError.message}</Notice>
          ) : !draft || !saved ? (
            <p className="text-sm text-zinc-500">Loading…</p>
          ) : (
            <div className="flex flex-col gap-4">
              {!canEdit && role && <Notice>You can view this layout. Owners and admins edit layouts.</Notice>}
              {canEdit && saved.locked && (
                <Notice tone="amber">
                  Event dates use this layout, so it can&apos;t be edited.{" "}
                  <button type="button" onClick={copy} disabled={busy !== null} className="font-medium underline">
                    {busy === "copy" ? "Copying…" : "Make an editable copy"}
                  </button>
                </Notice>
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
                <div className="grid gap-4 sm:grid-cols-2">
                  <NumberField
                    label="Canvas width (units)"
                    value={draft.canvasWidth}
                    disabled={!editable}
                    onChange={(v) => setDraft({ ...draft, canvasWidth: v })}
                  />
                  <NumberField
                    label="Canvas height (units)"
                    value={draft.canvasHeight}
                    disabled={!editable}
                    onChange={(v) => setDraft({ ...draft, canvasHeight: v })}
                  />
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
                  <PlanCanvas
                    stalls={draft.stalls}
                    canvasWidth={draft.canvasWidth}
                    canvasHeight={draft.canvasHeight}
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
                    <table className="w-full min-w-[26rem] text-left text-sm">
                      <thead className="text-xs text-zinc-500">
                        <tr>
                          <th className="py-1 pr-3 font-medium">Label</th>
                          <th className="py-1 pr-3 font-medium">Position</th>
                          <th className="py-1 pr-3 font-medium">Size</th>
                          <th className="py-1 font-medium">Real size</th>
                        </tr>
                      </thead>
                      <tbody>
                        {draft.stalls.map((s) => {
                          const issues = [...(problems[s.key] ?? []), ...(serverProblems[s.key] ?? [])];
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
                              <td className="py-1">
                                {physicalSize({
                                  physical_width: s.physicalWidth || null,
                                  physical_depth: s.physicalDepth || null,
                                  physical_unit: s.physicalUnit || null,
                                }) ?? "—"}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>

              {editable && (
                <div className="flex flex-wrap items-center gap-3">
                  <button type="button" onClick={save} disabled={!dirty || problemCount > 0 || busy !== null} className={buttonClass}>
                    {busy === "save" ? "Saving…" : "Save layout"}
                  </button>
                  {dirty && (
                    <button type="button" onClick={discard} disabled={busy !== null} className={secondaryButtonClass}>
                      Discard changes
                    </button>
                  )}
                  {problemCount > 0 && <span className="text-sm text-red-600">Fix {problemCount} problem(s) before saving.</span>}
                </div>
              )}
            </div>
          )}
        </AccountGate>
      </div>
    </DjangoPage>
  );
}

function StallPanel({
  stall,
  editable,
  problems,
  onChange,
}: {
  stall: DraftStall;
  editable: boolean;
  problems: string[];
  onChange: (change: Partial<DraftStall>) => void;
}) {
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
      </fieldset>
    </Card>
  );
}
