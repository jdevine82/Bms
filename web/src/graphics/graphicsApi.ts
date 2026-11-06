// Graphics (main spec 6.4, milestone 9A): the model as the API sends it, and the calls.

export type Colour = "grey" | "blue" | "cyan" | "green" | "amber" | "red";
export type XY = [number, number];

export interface ValueBand { below?: number; colour: Colour; flow?: boolean }
export type ColourRule =
  | { type: "follows"; item: string; colour?: Colour }
  | { type: "bands"; point: string; bands: ValueBand[]; hysteresis?: number }
  | { type: "fill"; point: string; colour?: Colour; min?: number; max?: number };

export interface SymbolBindings { run?: string; value?: string; fault?: string; command?: string }

export type GraphicItem =
  | { type: "symbol"; id: string; symbol: string; x: number; y: number; size?: number; rotate?: number;
      label?: string; bind?: SymbolBindings }
  | { type: "value"; id: string; x: number; y: number; point: string; label?: string; decimals?: number }
  | { type: "pipe" | "duct"; id: string; path: XY[]; width?: number; rule?: ColourRule }
  | { type: "room"; id: string; space: string; x: number; y: number; w: number; h: number; values?: string[];
      rule?: Extract<ColourRule, { type: "bands" }> }
  | { type: "text"; id: string; x: number; y: number; text: string; size?: number };

export interface GraphicBackground { svg: string; x?: number; y?: number; w: number; h: number; opacity?: number }

export interface Graphic {
  name: string;
  dis: string;
  kind: "site" | "room" | "system";
  space?: string;
  width?: number;
  height?: number;
  tab?: { order: number };
  background?: GraphicBackground;
  items: GraphicItem[];
}

export interface LibrarySymbol {
  id: string;
  dis: string;
  category: string;
  source: string; // "equinor:CODE", "devine" or "site"
  kind: "state" | "position" | "level" | "value";
  animation?: "none" | "spin" | "flash"; // site symbols: for the On group
  svg?: string; // site symbols: the cleaned SVG
  shapes?: SymbolShape[]; // site symbols drawn in the editor
  spin?: { cx: number; cy: number; r: number };
  spinWhole?: boolean;
  text?: string;
}

export interface SymbolShape {
  type: "line" | "polyline" | "rect" | "circle" | "text";
  layer: "off" | "on" | "both";
  points?: XY[];
  x?: number; y?: number; w?: number; h?: number; r?: number;
  text?: string; size?: number; fill?: boolean;
}

/** The inside and viewBox of a cleaned SVG. */
export function svgParts(svg: string): { inner: string; viewBox: string } {
  const open = /<svg[^>]*>/.exec(svg);
  const viewBox = open ? /viewBox="([^"]+)"/.exec(open[0])?.[1] ?? "0 0 64 64" : "0 0 64 64";
  return { inner: /<svg[^>]*>([\s\S]*)<\/svg>/.exec(svg)?.[1] ?? "", viewBox };
}

async function getJson<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} from ${url}`);
  return (await r.json()) as T;
}

export const getGraphics = () => getJson<{ source: "setup" | "generated"; graphics: Graphic[] }>("/api/graphics");
export const getLibrary = () => getJson<LibrarySymbol[]>("/api/graphics/library");

// Symbol SVGs are shipped files with no scripts (decision 025); their inside is drawn in place so
// they take the state colour (currentColor).
const svgCache = new Map<string, Promise<string>>();
export function symbolSvg(id: string): Promise<string> {
  let p = svgCache.get(id);
  if (!p) {
    p = fetch(`/symbols/${encodeURIComponent(id)}.svg`)
      .then((r) => (r.ok ? r.text() : ""))
      .then((t) => /<svg[^>]*>([\s\S]*)<\/svg>/.exec(t)?.[1] ?? "")
      .catch(() => "");
    svgCache.set(id, p);
  }
  return p;
}
