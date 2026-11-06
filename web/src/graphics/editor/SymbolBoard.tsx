// Drawing a site symbol (milestone 9B, decision 026): lines, polylines, rectangles, circles and
// text on the 64 x 64 grid, each in the Off layer, the On layer or both. The SVG is written here
// and cleaned by the server when it is saved.
import { useEffect, useRef, useState } from "react";
import type { SymbolShape, XY } from "../graphicsApi";

type DrawTool = "select" | SymbolShape["type"];
const STEP = 2; // snap to 2 units
const snap2 = (v: number) => Math.max(0, Math.min(64, Math.round(v / STEP) * STEP));
const esc = (t: string) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/** One shape as SVG (stroke in currentColor; filled shapes and text filled too). */
export function shapeSvg(s: SymbolShape): string {
  const fill = s.fill ? ' fill="currentColor"' : "";
  switch (s.type) {
    case "line": {
      const [a, b] = s.points ?? [];
      return a && b ? `<line x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}"/>` : "";
    }
    case "polyline":
      return `<polyline points="${(s.points ?? []).map((p) => p.join(",")).join(" ")}"${fill}/>`;
    case "rect":
      return `<rect x="${s.x}" y="${s.y}" width="${s.w}" height="${s.h}"${fill}/>`;
    case "circle":
      return `<circle cx="${s.x}" cy="${s.y}" r="${s.r}"${fill}/>`;
    case "text":
      return `<text x="${s.x}" y="${s.y}" font-size="${s.size ?? 12}" text-anchor="middle" dominant-baseline="middle" ` +
        `fill="currentColor" stroke="none" font-family="sans-serif">${esc(s.text ?? "")}</text>`;
  }
}

/** The whole symbol: shapes in both layers, then the Off group and the On group. */
export function symbolFromShapes(shapes: SymbolShape[]): string {
  const of = (layer: SymbolShape["layer"]) => shapes.filter((s) => s.layer === layer).map(shapeSvg).join("");
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><g fill="none" stroke="currentColor" ` +
    `stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${of("both")}<g id="off">${of("off")}</g>` +
    `<g id="on">${of("on")}</g></g></svg>`;
}

export function SymbolBoard({ shapes, onChange }: { shapes: SymbolShape[]; onChange: (s: SymbolShape[]) => void }) {
  const [tool, setTool] = useState<DrawTool>("line");
  const [layer, setLayer] = useState<SymbolShape["layer"]>("both");
  const [fill, setFill] = useState(false);
  const [text, setText] = useState("T");
  const [selected, setSelected] = useState<number | null>(null);
  const [draft, setDraft] = useState<SymbolShape | null>(null);
  const [show, setShow] = useState<"all" | "off" | "on">("all");
  const start = useRef<XY | null>(null);
  const board = useRef<SVGSVGElement>(null);

  const at = (e: { clientX: number; clientY: number }): XY => {
    const svg = board.current!;
    const pt = svg.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const p = pt.matrixTransform(svg.getScreenCTM()!.inverse());
    return [snap2(p.x), snap2(p.y)];
  };

  const shapeFrom = (a: XY, b: XY): SymbolShape | null => {
    if (tool === "line") return { type: "line", layer, points: [a, b] };
    if (tool === "rect") return { type: "rect", layer, fill, x: Math.min(a[0], b[0]), y: Math.min(a[1], b[1]),
                                  w: Math.abs(b[0] - a[0]), h: Math.abs(b[1] - a[1]) };
    if (tool === "circle") return { type: "circle", layer, fill, x: a[0], y: a[1], r: Math.round(Math.hypot(b[0] - a[0], b[1] - a[1])) };
    return null;
  };

  const down = (e: React.PointerEvent) => {
    e.preventDefault();
    (document.activeElement as HTMLElement | null)?.blur();
    const p = at(e);
    if (tool === "select") {
      const i = (e.target as Element).closest("[data-shape]")?.getAttribute("data-shape");
      setSelected(i == null ? null : Number(i));
      return;
    }
    if (tool === "text") {
      onChange([...shapes, { type: "text", layer, x: p[0], y: p[1], text, size: 14 }]);
      return;
    }
    if (tool === "polyline") {
      setDraft((d) => (d ? { ...d, points: [...(d.points ?? []), p] } : { type: "polyline", layer, fill, points: [p] }));
      return;
    }
    start.current = p;
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
  };
  const move = (e: React.PointerEvent) => {
    if (start.current) setDraft(shapeFrom(start.current, at(e)));
  };
  const up = (e: React.PointerEvent) => {
    if (!start.current) return;
    const s = shapeFrom(start.current, at(e));
    start.current = null;
    setDraft(null);
    const empty = !s || (s.type === "line" && s.points![0].join() === s.points![1].join())
      || (s.type === "rect" && (!s.w || !s.h)) || (s.type === "circle" && !s.r);
    if (s && !empty) onChange([...shapes, s]);
  };
  const finishPolyline = () => {
    if (draft?.type === "polyline" && (draft.points?.length ?? 0) >= 2) onChange([...shapes, draft]);
    setDraft(null);
  };

  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input, textarea, select")) return;
      if (e.key === "Enter" && draft) finishPolyline();
      if (e.key === "Escape") { setDraft(null); setSelected(null); }
      if ((e.key === "Delete" || e.key === "Backspace") && selected !== null) {
        e.preventDefault();
        onChange(shapes.filter((_, i) => i !== selected));
        setSelected(null);
      }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  });

  const visible = (s: SymbolShape) => show === "all" || s.layer === "both" || s.layer === show;
  const patchSelected = (patch: Partial<SymbolShape>) =>
    selected !== null && onChange(shapes.map((s, i) => (i === selected ? { ...s, ...patch } : s)));

  return (
    <div className="symbol-board">
      <div>
        <div className="palette-tools board-tools" role="toolbar" aria-label="Drawing tools">
          {(["select", "line", "polyline", "rect", "circle", "text"] as DrawTool[]).map((t) => (
            <button key={t} type="button" className={tool === t ? "active" : ""} aria-pressed={tool === t}
                    onClick={() => { setTool(t); setDraft(null); }}>
              {{ select: "Select", line: "Line", polyline: "Polyline", rect: "Rectangle", circle: "Circle", text: "Text" }[t]}
            </button>
          ))}
        </div>
        <svg ref={board} className="board" viewBox="0 0 64 64" aria-label="Symbol drawing board"
             onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}
             onDoubleClick={finishPolyline}>
          {Array.from({ length: 17 }, (_, i) => i * 4).map((v) => (
            <g key={v}><line className="board-grid" x1={v} y1={0} x2={v} y2={64} /><line className="board-grid" x1={0} y1={v} x2={64} y2={v} /></g>
          ))}
          <rect x={0} y={0} width={64} height={64} fill="transparent" />
          {shapes.map((s, i) => (
            <g key={i} data-shape={i} className={`board-shape layer-${s.layer}${selected === i ? " selected" : ""}${visible(s) ? "" : " dimmed"}`}
               fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"
               dangerouslySetInnerHTML={{ __html: shapeSvg(s) }} />
          ))}
          {draft && (
            <g className={`board-shape layer-${draft.layer}`} fill="none" stroke="currentColor" strokeWidth={2} strokeDasharray="2 1"
               dangerouslySetInnerHTML={{ __html: shapeSvg(draft) }} />
          )}
        </svg>
        <p className="muted small">
          Drag to draw lines, rectangles and circles; click the corners of a polyline and double-click to end it.
          Grey shapes are in the Off layer, green in the On layer, and the rest in both.
        </p>
      </div>
      <div>
        <div className="prop">
          <label htmlFor="board-layer">New shapes go in</label>
          <select id="board-layer" value={layer} onChange={(e) => setLayer(e.target.value as SymbolShape["layer"])}>
            <option value="both">Both layers (always shown)</option>
            <option value="off">The Off layer</option>
            <option value="on">The On layer</option>
          </select>
        </div>
        <label className="prop-inline"><input type="checkbox" checked={fill} onChange={(e) => setFill(e.target.checked)} /> Filled</label>
        {tool === "text" && (
          <div className="prop">
            <label htmlFor="board-text">Text</label>
            <input id="board-text" value={text} maxLength={40} onChange={(e) => setText(e.target.value)} />
          </div>
        )}
        <div className="prop">
          <label htmlFor="board-show">Show</label>
          <select id="board-show" value={show} onChange={(e) => setShow(e.target.value as typeof show)}>
            <option value="all">Both layers</option><option value="off">As Off</option><option value="on">As On</option>
          </select>
        </div>
        <ul className="shape-list" aria-label="Shapes">
          {shapes.map((s, i) => (
            <li key={i} className={selected === i ? "selected" : ""}>
              <button type="button" className="link" onClick={() => { setSelected(i); setTool("select"); }}>
                {i + 1}. {s.type}{s.type === "text" ? ` "${s.text}"` : ""}
              </button>
              <span className="muted">{s.layer}</span>
              <button type="button" className="link" aria-label={`Remove shape ${i + 1}`}
                      onClick={() => { onChange(shapes.filter((_, j) => j !== i)); setSelected(null); }}>×</button>
            </li>
          ))}
        </ul>
        {selected !== null && shapes[selected] && (
          <div className="prop">
            <label htmlFor="shape-layer">Selected shape's layer</label>
            <select id="shape-layer" value={shapes[selected].layer}
                    onChange={(e) => patchSelected({ layer: e.target.value as SymbolShape["layer"] })}>
              <option value="both">Both</option><option value="off">Off</option><option value="on">On</option>
            </select>
          </div>
        )}
      </div>
    </div>
  );
}
