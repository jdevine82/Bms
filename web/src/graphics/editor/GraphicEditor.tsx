// The graphics editor (main spec 6.4, milestone 9B): place library and site symbols, draw pipes,
// ducts and rooms on a 10-unit grid, bind points, set colour rules, and save as a checked setup
// change. The drawing shows live values while editing. Desktop browsers only.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { LiveValue, Point, Site } from "../../api";
import { getPoints, getSite } from "../../api";
import { useLive } from "../../live";
import { SetupError, put } from "../../setup/api";
import type { Outcome } from "../../setup/ui";
import { OutcomeBox, outcomeFrom } from "../../setup/ui";
import type { Graphic, GraphicItem, LibrarySymbol, XY } from "../graphicsApi";
import { getGraphics, getLibrary } from "../graphicsApi";
import { GraphicView } from "../GraphicView";
import { GRID, itemBox, moveItem, newId, resizeHandle, resizeTo, snap } from "./geometry";
import { Palette, type Tool } from "./Palette";
import { Properties } from "./Properties";
import "./editor.css";

const NEW: Graphic = { name: "", dis: "New graphic", kind: "system", width: 1000, height: 600, items: [] };

type Drag =
  | { mode: "move"; id: string; start: XY; before: Graphic; orig: GraphicItem }
  | { mode: "resize"; id: string; before: Graphic; orig: GraphicItem }
  | { mode: "vertex"; id: string; vertex: number; before: Graphic; orig: GraphicItem };

export function GraphicEditor({ name, onClose }: { name: string | null; onClose: () => void }) {
  const [g, setG] = useState<Graphic | null>(null);
  const [saved, setSaved] = useState<string>("");
  const [isNew, setIsNew] = useState(name === null);
  const [library, setLibrary] = useState<LibrarySymbol[]>([]);
  const [points, setPoints] = useState<Point[]>([]);
  const [site, setSite] = useState<Site | null>(null);
  const [live, setLive] = useState<Record<string, LiveValue>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [tool, setTool] = useState<Tool>({ kind: "select" });
  const [line, setLine] = useState<XY[] | null>(null);
  const [cursor, setCursor] = useState<XY | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [error, setError] = useState<string | null>(null);
  const past = useRef<Graphic[]>([]);
  const future = useRef<Graphic[]>([]);
  const drag = useRef<Drag | null>(null);
  const dragged = useRef<Graphic | null>(null); // the latest drag result (state may not have rendered yet)
  const overlay = useRef<SVGSVGElement>(null);
  const [, bump] = useState(0); // re-render after undo stacks change

  useEffect(() => {
    Promise.all([getGraphics(), getLibrary(), getPoints(), getSite()])
      .then(([gs, lib, pts, s]) => {
        setLibrary(lib);
        setPoints(pts);
        setSite(s);
        const found = name ? gs.graphics.find((x) => x.name === name) : null;
        if (name && !found) {
          setError(`There is no graphic '${name}'.`);
          return;
        }
        const start = found ?? NEW;
        setG(start);
        setSaved(found ? JSON.stringify(found) : "");
      })
      .catch((e: unknown) => setError(String(e)));
  }, [name]);

  const onValue = useCallback((v: LiveValue) => setLive((old) => ({ ...old, [v.id]: v })), []);
  useLive(points.length ? "all" : [], onValue);

  const dirty = g !== null && JSON.stringify(g) !== saved;
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  /** A change that can be undone. */
  const change = useCallback((next: Graphic, before?: Graphic) => {
    setG((cur) => {
      if (cur) {
        past.current.push(before ?? cur);
        if (past.current.length > 200) past.current.shift();
        future.current = [];
      }
      return next;
    });
    bump((n) => n + 1);
  }, []);
  const undo = () => {
    const prev = past.current.pop();
    if (prev && g) {
      future.current.push(g);
      setG(prev);
      bump((n) => n + 1);
    }
  };
  const redo = () => {
    const next = future.current.pop();
    if (next && g) {
      past.current.push(g);
      setG(next);
      bump((n) => n + 1);
    }
  };

  const item = g?.items.find((i) => i.id === selected) ?? null;
  const setItem = (next: GraphicItem, before?: Graphic) =>
    g && change({ ...g, items: g.items.map((i) => (i.id === next.id ? next : i)) }, before);
  const removeSelected = () => {
    if (!g || !selected) return;
    change({ ...g, items: g.items.filter((i) => i.id !== selected) });
    setSelected(null);
  };
  const duplicate = () => {
    if (!g || !item) return;
    const copy = { ...moveItem(item, GRID * 2, GRID * 2), id: newId(g.items, item.type === "symbol" ? item.symbol : item.type) } as GraphicItem;
    change({ ...g, items: [...g.items, copy] });
    setSelected(copy.id);
  };
  const finishLine = () => {
    if (g && line && line.length >= 2 && (tool.kind === "pipe" || tool.kind === "duct")) {
      const it: GraphicItem = { type: tool.kind, id: newId(g.items, tool.kind), path: line };
      change({ ...g, items: [...g.items, it] });
      setSelected(it.id);
    }
    setLine(null);
  };

  // Keyboard: not while typing in a field.
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.closest("input, textarea, select")) return;
      const ctrl = e.ctrlKey || e.metaKey;
      if (ctrl && e.key.toLowerCase() === "z" && !e.shiftKey) { e.preventDefault(); undo(); }
      else if (ctrl && (e.key.toLowerCase() === "y" || (e.key.toLowerCase() === "z" && e.shiftKey))) { e.preventDefault(); redo(); }
      else if (ctrl && e.key.toLowerCase() === "d") { e.preventDefault(); duplicate(); }
      else if (e.key === "Delete" || e.key === "Backspace") { if (selected) { e.preventDefault(); removeSelected(); } }
      else if (e.key === "Escape") { setLine(null); setTool({ kind: "select" }); setSelected(null); }
      else if (e.key === "Enter" && line) { e.preventDefault(); finishLine(); }
      else if (item && e.key.startsWith("Arrow")) {
        e.preventDefault();
        const step = e.altKey ? 1 : GRID;
        const dx = e.key === "ArrowLeft" ? -step : e.key === "ArrowRight" ? step : 0;
        const dy = e.key === "ArrowUp" ? -step : e.key === "ArrowDown" ? step : 0;
        setItem(moveItem(item, dx, dy));
      }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  });

  const toPoint = (e: { clientX: number; clientY: number }): XY => {
    const svg = overlay.current!;
    const pt = svg.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const p = pt.matrixTransform(svg.getScreenCTM()!.inverse());
    return [p.x, p.y];
  };

  const place = (p: XY) => {
    if (!g) return;
    const [x, y] = [snap(p[0]), snap(p[1])];
    let it: GraphicItem | null = null;
    if (tool.kind === "symbol") {
      const lib = library.find((s) => s.id === tool.symbol);
      it = { type: "symbol", id: newId(g.items, tool.symbol), symbol: tool.symbol, x, y, size: 64, label: lib?.dis };
    } else if (tool.kind === "room") {
      const space = site?.spaces?.[0]?.name ?? "";
      it = { type: "room", id: newId(g.items, "room"), space, x, y, w: 200, h: 120, values: [] };
    } else if (tool.kind === "value") {
      it = { type: "value", id: newId(g.items, "value"), x, y, point: points[0]?.name ?? "" };
    } else if (tool.kind === "text") {
      it = { type: "text", id: newId(g.items, "text"), x, y, text: "Label", size: 16 };
    }
    if (it) {
      change({ ...g, items: [...g.items, it] });
      setSelected(it.id);
      setTool({ kind: "select" });
    }
  };

  const down = (e: React.PointerEvent) => {
    if (!g || e.button !== 0) return;
    e.preventDefault(); // no text selection, so the browser never turns a drag into drag-and-drop
    (document.activeElement as HTMLElement | null)?.blur(); // so the editor's keys work, not a field's
    const p = toPoint(e);
    const target = e.target as Element;
    const handle = target.closest("[data-handle]");
    const hit = target.closest("[data-hit]");
    if (tool.kind === "pipe" || tool.kind === "duct") {
      const q: XY = [snap(p[0]), snap(p[1])];
      setLine((l) => (l ? (l.at(-1)![0] === q[0] && l.at(-1)![1] === q[1] ? l : [...l, q]) : [q]));
      return;
    }
    if (tool.kind !== "select") {
      place(p);
      return;
    }
    if (handle && item) {
      const h = handle.getAttribute("data-handle")!;
      drag.current = h === "resize"
        ? { mode: "resize", id: item.id, before: g, orig: item }
        : { mode: "vertex", id: item.id, vertex: Number(h), before: g, orig: item };
    } else if (hit) {
      const id = hit.getAttribute("data-hit")!;
      setSelected(id);
      drag.current = { mode: "move", id, start: p, before: g, orig: g.items.find((i) => i.id === id)! };
    } else {
      setSelected(null);
      return;
    }
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
  };

  const move = (e: React.PointerEvent) => {
    const p = toPoint(e);
    setCursor([snap(p[0]), snap(p[1])]);
    const d = drag.current;
    if (!d || !g) return;
    let next = d.orig;
    if (d.mode === "move") next = moveItem(d.orig, snap(p[0] - d.start[0]), snap(p[1] - d.start[1]));
    else if (d.mode === "resize") next = resizeTo(d.orig, p);
    else if (d.orig.type === "pipe" || d.orig.type === "duct") {
      const path = d.orig.path.map((q, i) => (i === d.vertex ? [snap(p[0]), snap(p[1])] as XY : q));
      next = { ...d.orig, path };
    }
    const base = d.before;
    const result = { ...base, items: base.items.map((i) => (i.id === d.id ? next : i)) };
    dragged.current = result;
    setG(result);
  };

  const up = () => {
    const d = drag.current;
    const result = dragged.current;
    drag.current = null;
    dragged.current = null;
    if (d && result && JSON.stringify(d.before) !== JSON.stringify(result)) change(result, d.before);
  };

  const save = async (check: boolean) => {
    if (!g) return;
    if (!/^[a-z][a-z0-9_]*$/.test(g.name)) {
      setOutcome({ kind: "error", message: "Give the graphic a name: lowercase letters, digits and _, starting with a letter." });
      return;
    }
    try {
      const r = await put(`/api/setup/graphics/${encodeURIComponent(g.name)}${check ? "?check=true" : ""}`, g);
      if (check) {
        setOutcome({ kind: "ok", message: "It passes the checks.", issues: r.warnings });
      } else {
        setOutcome({ kind: "ok", message: `Saved ${g.dis}; it is live.`, issues: r.warnings });
        const fresh = (await getGraphics()).graphics.find((x) => x.name === g.name) ?? g;
        setG(fresh); // the background comes back cleaned
        setSaved(JSON.stringify(fresh));
        if (isNew) {
          setIsNew(false);
          history.replaceState(null, "", `#/setup/graphics/${encodeURIComponent(g.name)}`);
        }
      }
    } catch (e) {
      setOutcome(e instanceof SetupError ? outcomeFrom(e) : { kind: "error", message: String(e) });
    }
  };

  const byName = useMemo(() => Object.fromEntries(points.map((p) => [p.name, p])), [points]);
  const libById = useMemo(() => Object.fromEntries(library.map((s) => [s.id, s])), [library]);
  const spaces = useMemo(() => Object.fromEntries((site?.spaces ?? []).map((s) => [s.name, s.dis])), [site]);

  if (error) return <div className="editor-msg"><p className="error">{error}</p><button type="button" className="btn" onClick={onClose}>Back</button></div>;
  if (!g) return <p className="muted editor-msg">Loading the editor…</p>;
  const w = g.width ?? 1000;
  const h = g.height ?? 600;
  const handle = item ? resizeHandle(item) : null;
  const placing = tool.kind !== "select";

  return (
    <div className="graphic-editor">
      <div className="editor-narrow" role="note">The graphics editor needs a wider screen (a desktop browser).</div>
      <header className="editor-bar">
        <button type="button" className="btn" onClick={() => (!dirty || confirm("Leave without saving your changes?")) && onClose()}>
          ← Graphics
        </button>
        <h2>{g.dis}{dirty && <span className="editor-dirty" title="Not saved"> ●</span>}</h2>
        <span className="editor-spacer" />
        <button type="button" className="btn" disabled={!past.current.length} onClick={undo} title="Ctrl+Z">Undo</button>
        <button type="button" className="btn" disabled={!future.current.length} onClick={redo} title="Ctrl+Y">Redo</button>
        <button type="button" className="btn" onClick={() => void save(true)}>Check</button>
        <button type="button" className="btn primary" onClick={() => void save(false)}>Save</button>
      </header>
      <OutcomeBox outcome={outcome} />
      <div className="editor-body">
        <Palette library={library} tool={tool} onTool={(t) => { setTool(t); setLine(null); }} />
        <div className={`editor-canvas${placing ? " placing" : ""}`}>
          <div className="editor-stage" style={{ aspectRatio: `${w} / ${h}` }}>
            <GraphicView graphic={g} library={libById} points={byName} live={live} alarmed={new Set()} spaces={spaces} />
            <svg ref={overlay} className="editor-overlay" viewBox={`0 0 ${w} ${h}`} aria-label="Drawing"
                 onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}
                 onPointerLeave={() => setCursor(null)}
                 onDoubleClick={() => line && finishLine()}>
              <defs>
                <pattern id="editor-grid" width={GRID} height={GRID} patternUnits="userSpaceOnUse">
                  <path d={`M ${GRID} 0 L 0 0 0 ${GRID}`} className="editor-grid-line" />
                </pattern>
              </defs>
              <rect width={w} height={h} fill="url(#editor-grid)" className="editor-grid" />
              {g.items.map((it) => {
                if (it.type === "pipe" || it.type === "duct") {
                  return (
                    <polyline key={it.id} data-hit={it.id} className="editor-hit-line"
                              points={it.path.map((p) => p.join(",")).join(" ")}
                              strokeWidth={(it.width ?? (it.type === "duct" ? 14 : 6)) + 12} />
                  );
                }
                const b = itemBox(it);
                return <rect key={it.id} data-hit={it.id} className="editor-hit" x={b.x} y={b.y} width={b.w} height={b.h} />;
              })}
              {item && (() => {
                const b = itemBox(item);
                return <rect className="editor-selection" x={b.x - 2} y={b.y - 2} width={b.w + 4} height={b.h + 4} />;
              })()}
              {handle && (
                <rect data-handle="resize" className="editor-handle" x={handle[0] - 5} y={handle[1] - 5} width={10} height={10}>
                  <title>Drag to resize</title>
                </rect>
              )}
              {item && (item.type === "pipe" || item.type === "duct") && item.path.map((p, i) => (
                <circle key={i} data-handle={String(i)} className="editor-handle" cx={p[0]} cy={p[1]} r={6} />
              ))}
              {line && (
                <polyline className="editor-drawing" points={[...line, ...(cursor ? [cursor] : [])].map((p) => p.join(",")).join(" ")} />
              )}
            </svg>
          </div>
          <p className="muted small editor-hint">
            {tool.kind === "pipe" || tool.kind === "duct"
              ? "Click each corner; double-click or Enter ends the line, Escape cancels."
              : placing
                ? "Click the drawing to place it. Escape cancels."
                : "Drag to move; drag the square to resize. Arrows nudge (Alt: 1 unit), Delete removes, Ctrl+D duplicates."}
            {cursor && ` · ${cursor[0]}, ${cursor[1]}`}
          </p>
        </div>
        <Properties graphic={g} item={item} points={points} library={library} site={site} isNew={isNew}
                    onGraphic={(next) => change(next)} onItem={(next) => setItem(next)}
                    onRemove={removeSelected} onDuplicate={duplicate} onError={(m) => setOutcome({ kind: "error", message: m })} />
      </div>
    </div>
  );
}
