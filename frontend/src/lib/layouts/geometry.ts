// Client-side mirror of backend/layouts/geometry.py, for instant feedback in
// the editor. Integer canvas units, origin top-left; touching edges is fine.

export type Rect = { x: number; y: number; width: number; height: number };

export function overlaps(a: Rect, b: Rect): boolean {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
}

export function fits(r: Rect, canvasWidth: number, canvasHeight: number): boolean {
  return r.x >= 0 && r.y >= 0 && r.width >= 1 && r.height >= 1 && r.x + r.width <= canvasWidth && r.y + r.height <= canvasHeight;
}

/** Keep a rectangle's position inside the canvas (used while dragging). */
export function clampPosition(r: Rect, canvasWidth: number, canvasHeight: number): { x: number; y: number } {
  return {
    x: Math.min(Math.max(0, Math.round(r.x)), Math.max(0, canvasWidth - r.width)),
    y: Math.min(Math.max(0, Math.round(r.y)), Math.max(0, canvasHeight - r.height)),
  };
}

/** First free top-left spot for a new size x size stall, scanning rows. */
export function freeSpot(rects: Rect[], size: number, canvasWidth: number, canvasHeight: number): { x: number; y: number } | null {
  const step = Math.max(1, size);
  for (let y = 0; y + size <= canvasHeight; y += step) {
    for (let x = 0; x + size <= canvasWidth; x += step) {
      const candidate = { x, y, width: size, height: size };
      if (!rects.some((r) => overlaps(r, candidate))) return { x, y };
    }
  }
  return null;
}
