// Geometry for the graphics editor (milestone 9B): grid snapping, item boxes, moving and resizing.
import type { GraphicItem, XY } from "../graphicsApi";

export const GRID = 10;
export const snap = (v: number, grid = GRID) => Math.round(v / grid) * grid;

export interface Box { x: number; y: number; w: number; h: number }

/** The area an item covers, for selecting and outlining (text widths are estimates). */
export function itemBox(item: GraphicItem): Box {
  switch (item.type) {
    case "symbol": {
      const s = item.size ?? 64;
      return { x: item.x - 6, y: item.y - 6, w: s + 12, h: s + 46 };
    }
    case "value": {
      const w = Math.max(60, ((item.label ?? "").length + 10) * 7.5);
      return { x: item.x - w / 2, y: item.y - 16, w, h: 22 };
    }
    case "text": {
      const size = item.size ?? 16;
      return { x: item.x, y: item.y - size, w: Math.max(20, item.text.length * size * 0.6), h: size * 1.3 };
    }
    case "room":
      return { x: item.x, y: item.y, w: item.w, h: item.h };
    case "pipe":
    case "duct": {
      const xs = item.path.map((p) => p[0]);
      const ys = item.path.map((p) => p[1]);
      const pad = (item.width ?? (item.type === "duct" ? 14 : 6)) / 2 + 4;
      return { x: Math.min(...xs) - pad, y: Math.min(...ys) - pad, w: Math.max(...xs) - Math.min(...xs) + pad * 2,
               h: Math.max(...ys) - Math.min(...ys) + pad * 2 };
    }
  }
}

export function moveItem(item: GraphicItem, dx: number, dy: number): GraphicItem {
  switch (item.type) {
    case "pipe":
    case "duct":
      return { ...item, path: item.path.map(([x, y]) => [x + dx, y + dy] as XY) };
    default:
      return { ...item, x: item.x + dx, y: item.y + dy };
  }
}

/** Where the resize handle sits (bottom right), for items that resize. */
export function resizeHandle(item: GraphicItem): XY | null {
  if (item.type === "symbol") return [item.x + (item.size ?? 64), item.y + (item.size ?? 64)];
  if (item.type === "room") return [item.x + item.w, item.y + item.h];
  return null;
}

export function resizeTo(item: GraphicItem, p: XY): GraphicItem {
  if (item.type === "symbol") {
    const size = Math.max(GRID * 2, Math.min(2000, snap(Math.max(p[0] - item.x, p[1] - item.y))));
    return { ...item, size };
  }
  if (item.type === "room") {
    return { ...item, w: Math.max(GRID * 4, snap(p[0] - item.x)), h: Math.max(GRID * 4, snap(p[1] - item.y)) };
  }
  return item;
}

/** A new item ID of the form <type>-<n>, unused in the graphic. */
export function newId(items: GraphicItem[], base: string): string {
  const used = new Set(items.map((i) => i.id));
  for (let n = 1; ; n++) {
    const id = `${base}-${n}`;
    if (!used.has(id)) return id;
  }
}
