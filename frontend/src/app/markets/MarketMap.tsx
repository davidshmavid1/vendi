"use client";

import "leaflet/dist/leaflet.css";
import type * as Leaflet from "leaflet";
import { useEffect, useRef, useState } from "react";
import { formatOccurrence, type Bounds, type DiscoveryOccurrence } from "@/lib/discovery/logic";

export type MapConfig = { tileUrl: string; attribution: string };

export type MapMarkerData = {
  id: number;
  name: string;
  city: string;
  region: string;
  latitude: string | number;
  longitude: string | number;
  next_occurrence: DiscoveryOccurrence;
};

export type MapFocus = {
  key: string;
  bounds?: Bounds;
  center?: { lat: number; lng: number; zoom: number };
};

type Props = {
  config: MapConfig;
  markers: MapMarkerData[];
  selectedId: number | null;
  focus: MapFocus;
  onSelect: (id: number) => void;
  onViewportChange: (bounds: Bounds, movedByUser: boolean) => void;
  onFail: () => void;
};

const MARKER = "h-4 w-4 rounded-full border-2 border-white bg-green-700 shadow ring-1 ring-green-900/40";
const MARKER_SELECTED = "h-5 w-5 rounded-full border-2 border-white bg-orange-600 shadow ring-2 ring-orange-900/60";

function popupContent(marker: MapMarkerData): HTMLElement {
  // Built with DOM APIs (textContent), never HTML strings, so market names
  // can't inject markup.
  const root = document.createElement("div");
  root.className = "text-sm";
  const name = document.createElement("strong");
  name.textContent = marker.name;
  const place = document.createElement("div");
  place.textContent = [marker.city, marker.region].filter(Boolean).join(", ");
  const next = document.createElement("div");
  next.textContent = `Next: ${formatOccurrence(marker.next_occurrence)}`;
  const link = document.createElement("a");
  link.href = `/markets/${marker.id}`;
  link.textContent = "View market details";
  link.className = "underline";
  root.append(name, place, next, link);
  return root;
}

/** Leaflet map of discovery markers. Loaded only in the browser. */
export default function MarketMap(props: Props) {
  const { config, markers, selectedId, focus } = props;
  const containerRef = useRef<HTMLDivElement>(null);
  const leafletRef = useRef<typeof Leaflet | null>(null);
  const mapRef = useRef<Leaflet.Map | null>(null);
  const layerRef = useRef<Leaflet.LayerGroup | null>(null);
  const markerRefs = useRef(new Map<number, Leaflet.Marker>());
  const programmaticMove = useRef(false);
  const callbacks = useRef(props);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    callbacks.current = props;
  });

  // Create the map once per tile configuration.
  useEffect(() => {
    let cancelled = false;
    let resizeObserver: ResizeObserver | null = null;
    const markerRegistry = markerRefs.current;
    import("leaflet")
      .then((L) => {
        if (cancelled || !containerRef.current) return;
        leafletRef.current = L;
        const map = L.map(containerRef.current, { worldCopyJump: true, keyboard: true });
        map.setView([39.8, -98.6], 4);
        let loadedTile = false;
        let tileErrors = 0;
        const tiles = L.tileLayer(config.tileUrl, { attribution: config.attribution, maxZoom: 19 });
        tiles.on("tileload", () => {
          loadedTile = true;
        });
        tiles.on("tileerror", () => {
          tileErrors += 1;
          if (!loadedTile && tileErrors >= 4) callbacks.current.onFail();
        });
        tiles.addTo(map);
        layerRef.current = L.layerGroup().addTo(map);
        const report = () => {
          const b = map.getBounds();
          callbacks.current.onViewportChange(
            { south: b.getSouth(), west: b.getWest(), north: b.getNorth(), east: b.getEast() },
            !programmaticMove.current,
          );
          programmaticMove.current = false;
        };
        map.on("moveend", report);
        // The map may start hidden (mobile list view) at zero size. Recompute
        // its size when the container is shown or resized, and report the now
        // real viewport so markers load.
        let wasEmpty = containerRef.current.clientWidth === 0 || containerRef.current.clientHeight === 0;
        resizeObserver = new ResizeObserver(([entry]) => {
          const { width, height } = entry.contentRect;
          const empty = width === 0 || height === 0;
          if (!empty) {
            // A resize is never a user move. invalidateSize reports through
            // moveend (synchronously) only when the size actually changed.
            programmaticMove.current = true;
            map.invalidateSize();
            if (wasEmpty && programmaticMove.current) report();
            programmaticMove.current = false;
          }
          wasEmpty = empty;
        });
        resizeObserver.observe(containerRef.current);
        mapRef.current = map;
        setReady(true);
      })
      .catch(() => callbacks.current.onFail());
    return () => {
      cancelled = true;
      resizeObserver?.disconnect();
      mapRef.current?.remove();
      mapRef.current = null;
      markerRegistry.clear();
    };
  }, [config.tileUrl, config.attribution]);

  // Move the view when the page asks (initial view, nearby search).
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    programmaticMove.current = true;
    if (focus.bounds) {
      const { south, west, north, east } = focus.bounds;
      map.fitBounds([
        [south, west],
        [north, east < west ? east + 360 : east],
      ]);
    } else if (focus.center) {
      map.setView([focus.center.lat, focus.center.lng], focus.center.zoom);
    } else {
      map.fire("moveend");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refocus only when the key changes
  }, [ready, focus.key]);

  // Draw markers.
  useEffect(() => {
    const L = leafletRef.current;
    const layer = layerRef.current;
    if (!ready || !L || !layer) return;
    layer.clearLayers();
    markerRefs.current.clear();
    for (const data of markers) {
      const marker = L.marker([Number(data.latitude), Number(data.longitude)], {
        icon: L.divIcon({ className: MARKER, iconSize: [16, 16] }),
        keyboard: true,
        title: data.name,
        alt: `${data.name} market`,
        riseOnHover: true,
      });
      marker.bindPopup(popupContent(data));
      marker.on("click", () => callbacks.current.onSelect(data.id));
      marker.addTo(layer);
      markerRefs.current.set(data.id, marker);
    }
  }, [markers, ready]);

  // Highlight and open the selected market.
  useEffect(() => {
    const L = leafletRef.current;
    if (!ready || !L) return;
    for (const [id, marker] of markerRefs.current) {
      const selected = id === selectedId;
      marker.setIcon(
        L.divIcon({ className: selected ? MARKER_SELECTED : MARKER, iconSize: selected ? [20, 20] : [16, 16] }),
      );
      if (selected) {
        marker.setZIndexOffset(1000);
        if (!marker.isPopupOpen()) marker.openPopup();
      } else {
        marker.setZIndexOffset(0);
      }
    }
  }, [selectedId, markers, ready]);

  return (
    <div
      ref={containerRef}
      role="region"
      aria-label="Map of markets. Use arrow keys to pan and Tab to reach markers."
      className="h-full min-h-[420px] w-full rounded-lg border border-zinc-200 dark:border-zinc-800"
    />
  );
}
