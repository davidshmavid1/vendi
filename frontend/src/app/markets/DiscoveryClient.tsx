"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Badge } from "@/components/ui";
import {
  filtersToParams,
  formatDistance,
  formatOccurrence,
  MARKET_TYPES,
  normalizeBounds,
  parseFilters,
  toApiParams,
  type Bounds,
  type DiscoveryFilters,
  type DiscoveryOccurrence,
  type MarketType,
  type Near,
} from "@/lib/discovery/logic";
import type { MapConfig, MapFocus, MapMarkerData } from "./MarketMap";

const MarketMap = dynamic(() => import("./MarketMap"), {
  ssr: false,
  loading: () => <MapNotice>Loading map…</MapNotice>,
});

const PAGE_SIZE = 20;
const SEARCH_DEBOUNCE_MS = 350;
const RADII = [10, 25, 50, 100];

type DiscoveryMarket = {
  id: number;
  name: string;
  market_type: MarketType;
  venue_name: string;
  city: string;
  region: string;
  latitude: string | null;
  longitude: string | null;
  distance_km: number | null;
  next_occurrence: DiscoveryOccurrence;
  upcoming_preview: DiscoveryOccurrence[];
};

type ListState = {
  key: string;
  status: "ready" | "error";
  items: DiscoveryMarket[];
  next: number | null;
};

type MapState = {
  key: string;
  status: "ready" | "error";
  items: MapMarkerData[];
  total: number;
  truncated: boolean;
};

async function getJson<T>(path: string, signal: AbortSignal): Promise<T> {
  // Public endpoints: no cookies or credentials are sent.
  const response = await fetch(path, { signal, credentials: "omit", headers: { Accept: "application/json" } });
  if (!response.ok) {
    let message = "Something went wrong.";
    try {
      message = (await response.json())?.error?.message ?? message;
    } catch {
      // keep the generic message
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

function sameBounds(a: Bounds | null, b: Bounds | null): boolean {
  if (!a || !b) return a === b;
  return a.south === b.south && a.west === b.west && a.north === b.north && a.east === b.east;
}

function MapNotice({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full min-h-[240px] items-center justify-center rounded-lg border border-dashed border-zinc-300 p-6 text-center text-sm text-zinc-600 dark:border-zinc-700 dark:text-zinc-400">
      <p>{children}</p>
    </div>
  );
}

export function DiscoveryClient({ map }: { map: MapConfig | null }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const filters = useMemo(() => parseFilters(new URLSearchParams(searchParams.toString())), [searchParams]);

  const [queryInput, setQueryInput] = useState(filters.q);
  const [near, setNear] = useState<Near | null>(null); // device location: never put in the URL
  const [radius, setRadius] = useState(25);
  const [locationMessage, setLocationMessage] = useState<string | null>(null);
  const [locating, setLocating] = useState(false);
  const [view, setView] = useState<"list" | "map">("list");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [viewport, setViewport] = useState<{ bounds: Bounds; movedByUser: boolean } | null>(null);
  const [mapFailed, setMapFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  const updateFilters = useCallback(
    (changes: Partial<DiscoveryFilters>) => {
      const query = filtersToParams({ ...filters, ...changes }).toString();
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [filters, pathname, router],
  );

  // Debounced text search.
  useEffect(() => {
    const trimmed = queryInput.trim();
    if (trimmed === filters.q) return;
    const timer = setTimeout(() => updateFilters({ q: trimmed }), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [queryInput, filters.q, updateFilters]);

  // --- List results -------------------------------------------------------------------
  const listQuery = toApiParams(filters, { near, limit: PAGE_SIZE }).toString();
  const listKey = `${listQuery}#${attempt}`;
  const [list, setList] = useState<ListState | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<string | null>(null);
  const moreController = useRef<AbortController | null>(null);

  useEffect(() => {
    // A new search aborts the previous request, so an older response can
    // never replace newer results.
    const controller = new AbortController();
    moreController.current?.abort();
    getJson<{ items: DiscoveryMarket[]; next_cursor: number | null }>(
      `/api/v1/public/markets?${listQuery}`,
      controller.signal,
    )
      .then((page) => setList({ key: listKey, status: "ready", items: page.items, next: page.next_cursor }))
      .catch(() => {
        if (!controller.signal.aborted) setList({ key: listKey, status: "error", items: [], next: null });
      });
    return () => controller.abort();
  }, [listKey, listQuery]);

  const current = list?.key === listKey ? list : null;

  async function loadMore() {
    if (!current || current.next === null) return;
    const key = listKey;
    const controller = new AbortController();
    moreController.current?.abort();
    moreController.current = controller;
    setLoadingMore(true);
    setMoreError(null);
    try {
      const page = await getJson<{ items: DiscoveryMarket[]; next_cursor: number | null }>(
        `/api/v1/public/markets?${toApiParams(filters, { near, limit: PAGE_SIZE, cursor: current.next })}`,
        controller.signal,
      );
      setList((state) =>
        state && state.key === key
          ? { ...state, items: [...state.items, ...page.items], next: page.next_cursor }
          : state,
      );
    } catch {
      if (!controller.signal.aborted) setMoreError("Couldn't load more markets. Try again.");
    } finally {
      if (moreController.current === controller) setLoadingMore(false);
    }
  }

  // --- Map markers (current viewport, same filters) ---------------------------------------------
  const mapAvailable = map !== null && !mapFailed;
  const mapQuery = mapAvailable && viewport ? toApiParams(filters, { area: viewport.bounds, near }).toString() : null;
  const [mapState, setMapState] = useState<MapState | null>(null);

  useEffect(() => {
    if (!mapQuery) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      getJson<{ items: MapMarkerData[]; total: number; truncated: boolean }>(
        `/api/v1/public/markets/map?${mapQuery}`,
        controller.signal,
      )
        .then((result) => setMapState({ key: mapQuery, status: "ready", ...result }))
        .catch(() => {
          if (!controller.signal.aborted)
            setMapState({ key: mapQuery, status: "error", items: [], total: 0, truncated: false });
        });
    }, 250);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [mapQuery]);

  const mapCurrent = mapState?.key === mapQuery ? mapState : null;
  const markers = useMemo(() => mapCurrent?.items ?? [], [mapCurrent]);

  // Where the map starts, and where it jumps for a nearby search.
  const [initialArea] = useState(filters.area);
  const focus: MapFocus = useMemo(() => {
    if (near) return { key: `near:${near.lat},${near.lng},${near.radiusKm}`, center: { lat: near.lat, lng: near.lng, zoom: 10 } };
    if (initialArea) return { key: "initial-area", bounds: initialArea };
    return { key: "initial" };
  }, [near, initialArea]);

  const handleViewport = useCallback((raw: Bounds, movedByUser: boolean) => {
    setViewport((prev) => ({ bounds: normalizeBounds(raw), movedByUser: movedByUser || (prev?.movedByUser ?? false) }));
  }, []);

  const showSearchArea =
    mapAvailable && viewport !== null && viewport.movedByUser && !sameBounds(viewport.bounds, filters.area);

  function searchThisArea() {
    if (!viewport) return;
    setNear(null);
    setViewport({ ...viewport, movedByUser: false });
    updateFilters({ area: viewport.bounds });
  }

  // --- Location ----------------------------------------------------------------------------
  function locateMe() {
    setLocationMessage(null);
    if (typeof navigator === "undefined" || !("geolocation" in navigator)) {
      setLocationMessage("Your browser can't share a location. Search by city or region instead.");
      return;
    }
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setLocating(false);
        setNear({ lat: position.coords.latitude, lng: position.coords.longitude, radiusKm: radius });
        if (filters.area) updateFilters({ area: null });
      },
      (error) => {
        setLocating(false);
        setLocationMessage(
          error.code === error.PERMISSION_DENIED
            ? "Location access was denied. Search by city or region instead."
            : "We couldn't get your location. Search by city or region instead.",
        );
      },
      { timeout: 10_000, maximumAge: 300_000 },
    );
  }

  function selectFromList(id: number) {
    setSelectedId(id);
    setView("map");
  }

  function selectFromMap(id: number) {
    setSelectedId(id);
    document.getElementById(`market-${id}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  const hasFilters = Boolean(filters.q || filters.type || filters.from || filters.to || filters.area || near);
  const inputClass =
    "rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm outline-none focus:border-zinc-500 dark:border-zinc-700 dark:bg-zinc-950";

  let statusText = "Loading markets…";
  if (current?.status === "error") statusText = "Markets couldn't be loaded.";
  else if (current) {
    const count = current.items.length;
    statusText =
      count === 0
        ? "No markets found."
        : `${count}${current.next !== null ? "+" : ""} market${count === 1 ? "" : "s"} with upcoming dates`;
  }

  return (
    <div className="mt-4 flex flex-1 flex-col gap-4">
      <form
        role="search"
        className="grid grid-cols-1 gap-3 rounded-lg border border-zinc-200 bg-white p-4 sm:grid-cols-2 lg:grid-cols-6 dark:border-zinc-800 dark:bg-zinc-900"
        onSubmit={(event) => {
          event.preventDefault();
          updateFilters({ q: queryInput.trim() });
        }}
      >
        <label className="flex flex-col gap-1 text-sm lg:col-span-2">
          <span className="font-medium">Search</span>
          <input
            type="search"
            value={queryInput}
            onChange={(event) => setQueryInput(event.target.value)}
            placeholder="Market, venue, city or region"
            maxLength={100}
            className={inputClass}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">Type</span>
          <select
            value={filters.type}
            onChange={(event) => updateFilters({ type: event.target.value as MarketType | "" })}
            className={inputClass}
          >
            <option value="">All types</option>
            {Object.entries(MARKET_TYPES).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">From</span>
          <input type="date" value={filters.from} onChange={(e) => updateFilters({ from: e.target.value })} className={inputClass} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">To</span>
          <input
            type="date"
            value={filters.to}
            min={filters.from || undefined}
            onChange={(e) => updateFilters({ to: e.target.value })}
            className={inputClass}
          />
        </label>
        <div className="flex flex-col gap-1 text-sm">
          <span className="font-medium">Near me</span>
          {near ? (
            <div className="flex gap-2">
              <select
                aria-label="Distance from your location"
                value={radius}
                onChange={(e) => {
                  const next = Number(e.target.value);
                  setRadius(next);
                  setNear({ ...near, radiusKm: next });
                }}
                className={inputClass}
              >
                {RADII.map((km) => (
                  <option key={km} value={km}>
                    Within {km} km
                  </option>
                ))}
              </select>
              <button type="button" onClick={() => setNear(null)} className="text-sm underline">
                Clear
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={locateMe}
              disabled={locating}
              className="rounded-md border border-zinc-300 px-3 py-2 text-left text-sm hover:bg-zinc-100 disabled:opacity-60 dark:border-zinc-700 dark:hover:bg-zinc-800"
            >
              {locating ? "Finding you…" : "Use my location"}
            </button>
          )}
        </div>
        {(locationMessage || hasFilters) && (
          <div className="flex flex-wrap items-center gap-3 text-sm sm:col-span-2 lg:col-span-6">
            {locationMessage && (
              <p role="alert" className="text-amber-700 dark:text-amber-400">
                {locationMessage}
              </p>
            )}
            {filters.area && <Badge tone="blue">Searching a map area</Badge>}
            {hasFilters && (
              <button
                type="button"
                className="underline"
                onClick={() => {
                  setQueryInput("");
                  setNear(null);
                  router.replace(pathname, { scroll: false });
                }}
              >
                Clear all filters
              </button>
            )}
          </div>
        )}
      </form>

      <div className="flex gap-2 md:hidden" role="group" aria-label="Show results as">
        {(["list", "map"] as const).map((mode) => (
          <button
            key={mode}
            type="button"
            aria-pressed={view === mode}
            onClick={() => setView(mode)}
            className={`flex-1 rounded-md border px-3 py-2 text-sm font-medium ${
              view === mode
                ? "border-zinc-900 bg-zinc-900 text-white dark:border-white dark:bg-white dark:text-zinc-900"
                : "border-zinc-300 dark:border-zinc-700"
            }`}
          >
            {mode === "list" ? "List" : "Map"}
          </button>
        ))}
      </div>

      <div className="grid flex-1 grid-cols-1 gap-4 md:grid-cols-2">
        <section aria-label="Market results" className={view === "map" ? "hidden md:block" : ""}>
          <p aria-live="polite" className="mb-3 text-sm text-zinc-600 dark:text-zinc-400">
            {statusText}
          </p>
          {current?.status === "error" && (
            <button type="button" className="text-sm underline" onClick={() => setAttempt((n) => n + 1)}>
              Try again
            </button>
          )}
          {current?.status === "ready" && current.items.length === 0 && (
            <p className="text-sm">Try a wider area, different dates, or fewer filters.</p>
          )}
          <ul className="flex flex-col gap-3">
            {current?.items.map((market) => {
              const distance = formatDistance(market.distance_km);
              const hasCoordinates = market.latitude !== null && market.longitude !== null;
              const later = market.upcoming_preview.slice(1);
              return (
                <li
                  key={market.id}
                  id={`market-${market.id}`}
                  className={`rounded-lg border bg-white p-4 dark:bg-zinc-900 ${
                    selectedId === market.id
                      ? "border-orange-500 ring-2 ring-orange-200 dark:ring-orange-900"
                      : "border-zinc-200 dark:border-zinc-800"
                  }`}
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Link href={`/markets/${market.id}`} className="font-semibold underline-offset-2 hover:underline">
                      {market.name}
                    </Link>
                    <Badge tone={market.market_type === "POPUP" ? "yellow" : "green"}>
                      {MARKET_TYPES[market.market_type]}
                    </Badge>
                    {distance && <span className="text-xs text-zinc-500">{distance}</span>}
                  </div>
                  <p className="text-sm text-zinc-600 dark:text-zinc-400">
                    {[market.venue_name, [market.city, market.region].filter(Boolean).join(", ")].filter(Boolean).join(" · ")}
                  </p>
                  <p className="mt-2 text-sm">
                    <span className="font-medium">Next:</span> {formatOccurrence(market.next_occurrence)}
                  </p>
                  {later.length > 0 && (
                    <p className="text-xs text-zinc-500">
                      Also: {later.map((o) => formatOccurrence(o).split(" · ")[0]).join(", ")}
                    </p>
                  )}
                  {mapAvailable && hasCoordinates && (
                    <button type="button" className="mt-2 text-xs underline" onClick={() => selectFromList(market.id)}>
                      Show on map
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
          {current?.next !== null && current?.status === "ready" && (
            <div className="mt-4">
              <button
                type="button"
                onClick={loadMore}
                disabled={loadingMore}
                className="rounded-md border border-zinc-300 px-4 py-2 text-sm font-medium hover:bg-zinc-100 disabled:opacity-60 dark:border-zinc-700 dark:hover:bg-zinc-800"
              >
                {loadingMore ? "Loading…" : "Load more markets"}
              </button>
              {moreError && (
                <p role="alert" className="mt-2 text-sm text-red-600">
                  {moreError}
                </p>
              )}
            </div>
          )}
        </section>

        <section
          aria-label="Map"
          className={`relative min-h-[420px] ${view === "list" ? "hidden md:block" : ""} md:sticky md:top-4 md:h-[calc(100vh-2rem)]`}
        >
          {!map ? (
            <MapNotice>The map isn&apos;t set up here, so results are shown as a list.</MapNotice>
          ) : mapFailed ? (
            <MapNotice>The map couldn&apos;t load. Results are still available in the list.</MapNotice>
          ) : (
            <>
              <MarketMap
                config={map}
                markers={markers}
                selectedId={selectedId}
                focus={focus}
                onSelect={selectFromMap}
                onViewportChange={handleViewport}
                onFail={() => setMapFailed(true)}
              />
              <div className="pointer-events-none absolute inset-x-0 top-3 z-[1000] flex flex-col items-center gap-2 px-3">
                {showSearchArea && (
                  <button
                    type="button"
                    onClick={searchThisArea}
                    className="pointer-events-auto rounded-full bg-zinc-900 px-4 py-2 text-sm font-medium text-white shadow dark:bg-white dark:text-zinc-900"
                  >
                    Search this area
                  </button>
                )}
                {mapCurrent?.truncated && (
                  <p role="status" className="pointer-events-auto rounded-md bg-amber-100 px-3 py-1 text-xs text-amber-900 shadow">
                    Showing {mapCurrent.items.length} of {mapCurrent.total} markets here. Zoom in or narrow your search to see
                    them all.
                  </p>
                )}
                {mapCurrent?.status === "error" && (
                  <p role="status" className="pointer-events-auto rounded-md bg-red-100 px-3 py-1 text-xs text-red-900 shadow">
                    Map results couldn&apos;t be loaded.
                  </p>
                )}
                {mapCurrent?.status === "ready" && mapCurrent.items.length === 0 && (
                  <p role="status" className="pointer-events-auto rounded-md bg-white px-3 py-1 text-xs shadow dark:bg-zinc-800">
                    No mapped markets in this view.
                  </p>
                )}
              </div>
              <p className="mt-2 text-xs text-zinc-500">Markets without a map location appear in the list only.</p>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
