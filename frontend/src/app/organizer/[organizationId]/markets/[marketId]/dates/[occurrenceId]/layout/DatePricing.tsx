"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge, Card } from "@/components/ui";
import { AccountGate, buttonClass, DjangoPage, inputClass, Notice, secondaryButtonClass } from "@/components/DjangoPage";
import { PlanCanvas } from "@/components/layouts/PlanCanvas";
import { apiGet, apiSend, type ApiError } from "@/lib/django/client";
import { useAccount } from "@/lib/django/useAccount";
import type { MyOrganization } from "@/lib/applications/types";
import { CURRENCY_EXPONENTS, formatMinor, inputToMinor, minorToInput } from "@/lib/layouts/money";
import { physicalSize, type DateLayoutOut, type VersionOut, type VersionSummary } from "@/lib/layouts/types";

type Row = { price: string; enabled: boolean };
type Draft = { versionId: number | null; currency: string; rows: Record<number, Row> };

const EMPTY: Draft = { versionId: null, currency: "USD", rows: {} };

function fromSaved(saved: DateLayoutOut | null): Draft {
  if (!saved) return EMPTY;
  const exponent = saved.currency_exponent ?? 2;
  return {
    versionId: saved.layout_version.id,
    currency: saved.currency ?? "USD",
    rows: Object.fromEntries(
      saved.offers.map((o) => [o.stall_id, { price: minorToInput(o.price_minor, exponent), enabled: o.enabled }]),
    ),
  };
}

type Props = { organizationId: number; marketId: number; occurrenceId: number };

/** One date's stall layout: which layout version it uses, and each stall's price. */
export function DatePricing({ organizationId, marketId, occurrenceId }: Props) {
  const { state: account, reload } = useAccount();
  const marketBase = `/organizations/${organizationId}/markets/${marketId}`;
  const base = `${marketBase}/occurrences/${occurrenceId}/layout`;
  const back = `/organizer/${organizationId}/markets/${marketId}`;
  const [role, setRole] = useState<MyOrganization["role"] | null>(null);
  const [saved, setSaved] = useState<DateLayoutOut | null>(null);
  const [versions, setVersions] = useState<VersionSummary[]>([]);
  const [version, setVersion] = useState<VersionOut | null>(null);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "failed">("loading");
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState<null | "save" | "publish" | "unpublish">(null);
  const [serverError, setServerError] = useState<ApiError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const signedIn = account.status === "signed_in";

  const load = useCallback(async () => {
    setLoadState("loading");
    const [layout, list, membership] = await Promise.all([
      apiGet<DateLayoutOut>(base),
      apiGet<{ items: VersionSummary[] }>(`${marketBase}/layout-versions`),
      apiGet<MyOrganization>(`/organizations/${organizationId}`),
    ]);
    if (membership.ok) setRole(membership.data.role);
    setServerError(null);
    if (!list.ok) {
      setLoadError(list.error);
      setLoadState("failed");
      return;
    }
    setVersions(list.data.items);
    if (layout.ok) {
      setSaved(layout.data);
      setVersion(layout.data.layout_version);
      setDraft(fromSaved(layout.data));
      setLoadState("ready");
    } else if (layout.error.code === "layout_not_found") {
      setSaved(null);
      setVersion(null);
      setDraft(EMPTY);
      setLoadState("ready");
    } else {
      setLoadError(layout.error);
      setLoadState("failed");
    }
  }, [base, marketBase, organizationId]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial load once signed in
    if (signedIn) void load();
  }, [signedIn, load]);

  const baseline = useMemo(() => JSON.stringify(fromSaved(saved)), [saved]);
  const dirty = JSON.stringify(draft) !== baseline;
  const canEdit = role === "OWNER" || role === "ADMIN";
  const published = saved?.published ?? false;
  const editable = canEdit && !published;
  const exponent = CURRENCY_EXPONENTS[draft.currency] ?? 2;

  const problems = useMemo(() => {
    const out: Record<number, string> = {};
    for (const stall of version?.stalls ?? []) {
      const row = draft.rows[stall.id];
      if (!row || inputToMinor(row.price, exponent) === null) {
        out[stall.id] = exponent === 0 ? "Enter a whole amount." : `Enter an amount with up to ${exponent} decimals.`;
      }
    }
    return out;
  }, [draft, version, exponent]);
  const problemCount = Object.keys(problems).length;

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  async function chooseVersion(id: number) {
    setNotice(null);
    const result = await apiGet<VersionOut>(`${marketBase}/layout-versions/${id}`);
    if (!result.ok) {
      setServerError(result.error);
      return;
    }
    // Carry prices over to stalls with the same label in the new layout.
    const byLabel = new Map((version?.stalls ?? []).map((s) => [s.label.toLowerCase(), draft.rows[s.id]]));
    const rows: Record<number, Row> = {};
    for (const stall of result.data.stalls) {
      rows[stall.id] = byLabel.get(stall.label.toLowerCase()) ?? { price: "", enabled: true };
    }
    setVersion(result.data);
    setSelected(null);
    setDraft({ ...draft, versionId: id, rows });
  }

  function setRow(stallId: number, change: Partial<Row>) {
    setDraft((d) => ({ ...d, rows: { ...d.rows, [stallId]: { ...d.rows[stallId], ...change } } }));
    setNotice(null);
  }

  async function save() {
    if (!version || problemCount > 0) return;
    setBusy("save");
    setServerError(null);
    const result = await apiSend<DateLayoutOut>("PUT", base, {
      expected_revision: saved?.revision ?? null,
      layout_version_id: version.id,
      currency: draft.currency,
      offers: version.stalls.map((s) => ({
        stall_id: s.id,
        price_minor: inputToMinor(draft.rows[s.id].price, exponent) ?? 0,
        enabled: draft.rows[s.id].enabled,
      })),
    });
    setBusy(null);
    if (result.ok) {
      setSaved(result.data);
      setVersion(result.data.layout_version);
      setDraft(fromSaved(result.data));
      setVersions((list) => list.map((v) => (v.id === result.data.layout_version.id ? { ...v, locked: true } : v)));
      setNotice("Stalls and prices saved.");
    } else setServerError(result.error);
  }

  async function setPublished(publish: boolean) {
    if (!saved) return;
    const question = publish
      ? "Publish this date's stalls and prices? Vendors will be able to see them."
      : "Unpublish this date's stalls and prices? Vendors won't see them until you publish again.";
    if (!window.confirm(question)) return;
    setBusy(publish ? "publish" : "unpublish");
    setServerError(null);
    const result = await apiSend<DateLayoutOut>(
      "POST",
      `${base}/${publish ? "publish" : "unpublish"}`,
      publish ? { expected_revision: saved.revision } : {},
    );
    setBusy(null);
    if (result.ok) {
      setSaved(result.data);
      setDraft(fromSaved(result.data));
      setNotice(publish ? "Published." : "Unpublished. You can change stalls and prices now.");
    } else setServerError(result.error);
  }

  function discard() {
    if (!window.confirm("Discard your unsaved changes?")) return;
    setDraft(fromSaved(saved));
    setVersion(saved?.layout_version ?? null);
    setServerError(null);
  }

  async function reloadLatest() {
    if (dirty && !window.confirm("Load the latest saved version? Your unsaved changes will be lost.")) return;
    await load();
  }

  const selectedDraft = versions.find((v) => v.id === draft.versionId);
  const planStalls = (version?.stalls ?? []).map((s) => ({
    key: String(s.id),
    label: s.label,
    x: s.x,
    y: s.y,
    width: s.width,
    height: s.height,
    muted: !draft.rows[s.id]?.enabled,
  }));

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
        <h1 className="text-2xl font-semibold tracking-tight">Stalls and prices for this date</h1>
        {saved && <Badge tone={published ? "green" : "zinc"}>{published ? "Published: vendors can see it" : "Not published"}</Badge>}
        {dirty && <Badge tone="yellow">Unsaved changes</Badge>}
      </div>
      <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
        Choose the stall layout this date uses and set each stall&apos;s price. Prices here apply to this date only.
        Listing stalls doesn&apos;t reserve or sell them.
      </p>

      <div className="mt-6">
        <AccountGate account={account} retry={reload}>
          {loadState === "loading" ? (
            <p className="text-sm text-zinc-500">Loading…</p>
          ) : loadState === "failed" ? (
            <Notice tone="red">
              {loadError?.message}{" "}
              <button type="button" className="underline" onClick={load}>
                Try again
              </button>
            </Notice>
          ) : (
            <div className="flex flex-col gap-4">
              {!canEdit && role && <Notice>You can view this date&apos;s stalls. Owners and admins set prices and publish.</Notice>}
              {canEdit && published && <Notice tone="amber">This date is published. Unpublish it to change stalls or prices.</Notice>}
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
                  <label className="flex flex-col gap-1 text-sm">
                    <span className="font-medium">Stall layout</span>
                    <select
                      value={draft.versionId ?? ""}
                      disabled={!editable}
                      onChange={(e) => void chooseVersion(Number(e.target.value))}
                      className={inputClass}
                    >
                      <option value="" disabled>
                        Choose a layout
                      </option>
                      {versions.map((v) => (
                        <option key={v.id} value={v.id} disabled={v.stall_count === 0}>
                          Layout v{v.number} · {v.stall_count} stalls{v.locked ? "" : " · draft"}
                          {v.stall_count === 0 ? " (no stalls)" : ""}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="flex flex-col gap-1 text-sm">
                    <span className="font-medium">Currency</span>
                    <select
                      value={draft.currency}
                      disabled={!editable}
                      onChange={(e) => setDraft({ ...draft, currency: e.target.value })}
                      className={inputClass}
                    >
                      {Object.keys(CURRENCY_EXPONENTS).map((c) => (
                        <option key={c}>{c}</option>
                      ))}
                    </select>
                  </label>
                </div>
                {versions.length === 0 && (
                  <p className="mt-3 text-sm">
                    This market has no stall layouts yet.{" "}
                    <Link href={back} className="underline">
                      Create one first
                    </Link>
                    .
                  </p>
                )}
                {editable && selectedDraft && !selectedDraft.locked && (
                  <p className="mt-3 text-xs text-amber-700 dark:text-amber-400">
                    Saving makes layout v{selectedDraft.number} read-only, because this date will use it. Copy it later to plan
                    changes.
                  </p>
                )}
              </Card>

              {version && (
                <>
                  <Card className="!p-3">
                    <PlanCanvas
                      stalls={planStalls}
                      canvasWidth={version.canvas_width}
                      canvasHeight={version.canvas_height}
                      selected={selected}
                      invalid={new Set(Object.keys(problems))}
                      editable={false}
                      onSelect={setSelected}
                      onMove={() => {}}
                    />
                  </Card>
                  <Card>
                    <h2 className="text-sm font-semibold">Prices for layout v{version.number}</h2>
                    <div className="mt-2 overflow-x-auto">
                      <table className="w-full min-w-[30rem] text-left text-sm">
                        <thead className="text-xs text-zinc-500">
                          <tr>
                            <th className="py-1 pr-3 font-medium">Stall</th>
                            <th className="py-1 pr-3 font-medium">Size</th>
                            <th className="py-1 pr-3 font-medium">Price ({draft.currency})</th>
                            <th className="py-1 font-medium">Offered</th>
                          </tr>
                        </thead>
                        <tbody>
                          {version.stalls.map((s) => {
                            const row = draft.rows[s.id] ?? { price: "", enabled: true };
                            const minor = inputToMinor(row.price, exponent);
                            return (
                              <tr
                                key={s.id}
                                className={`border-t border-zinc-200 dark:border-zinc-800 ${
                                  selected === String(s.id) ? "bg-orange-50 dark:bg-zinc-800" : ""
                                }`}
                              >
                                <th scope="row" className="py-1 pr-3 font-medium">
                                  <button type="button" onClick={() => setSelected(String(s.id))} className="underline">
                                    {s.label}
                                  </button>
                                </th>
                                <td className="py-1 pr-3">{physicalSize(s) ?? `${s.width} × ${s.height} units`}</td>
                                <td className="py-1 pr-3">
                                  <input
                                    aria-label={`Price for stall ${s.label}`}
                                    inputMode="decimal"
                                    value={row.price}
                                    disabled={!editable}
                                    onChange={(e) => setRow(s.id, { price: e.target.value })}
                                    aria-invalid={Boolean(problems[s.id])}
                                    className={`${inputClass} max-w-[9rem]`}
                                  />
                                  {problems[s.id] ? (
                                    <span className="block text-xs text-red-600">{problems[s.id]}</span>
                                  ) : (
                                    minor !== null && (
                                      <span className="block text-xs text-zinc-500">{formatMinor(minor, draft.currency, exponent)}</span>
                                    )
                                  )}
                                </td>
                                <td className="py-1">
                                  <input
                                    type="checkbox"
                                    aria-label={`Offer stall ${s.label} on this date`}
                                    checked={row.enabled}
                                    disabled={!editable}
                                    onChange={(e) => setRow(s.id, { enabled: e.target.checked })}
                                  />
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                    <p className="mt-2 text-xs text-zinc-500">
                      Listed prices only; taxes and fees aren&apos;t added. Unchecked stalls show as &ldquo;Not offered&rdquo;.
                    </p>
                  </Card>
                </>
              )}

              {canEdit && (
                <div className="flex flex-wrap items-center gap-3">
                  {!published && (
                    <>
                      <button
                        type="button"
                        onClick={save}
                        disabled={!dirty || !version || problemCount > 0 || busy !== null}
                        className={buttonClass}
                      >
                        {busy === "save" ? "Saving…" : "Save stalls and prices"}
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
                  {problemCount > 0 && version && <span className="text-sm text-red-600">Set a valid price for every stall.</span>}
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
