// The editor's palette: drawing tools, then library and site symbols by category.
import { useEffect, useState } from "react";
import type { LibrarySymbol } from "../graphicsApi";
import { svgParts, symbolSvg } from "../graphicsApi";

export type Tool =
  | { kind: "select" }
  | { kind: "symbol"; symbol: string }
  | { kind: "pipe" | "duct" | "room" | "value" | "text" };

const TOOLS: { kind: Exclude<Tool["kind"], "symbol">; label: string }[] = [
  { kind: "select", label: "Select" },
  { kind: "pipe", label: "Pipe" },
  { kind: "duct", label: "Duct" },
  { kind: "room", label: "Room" },
  { kind: "value", label: "Live value" },
  { kind: "text", label: "Text" },
];

export function Palette({ library, tool, onTool }: { library: LibrarySymbol[]; tool: Tool; onTool: (t: Tool) => void }) {
  const categories = new Map<string, LibrarySymbol[]>();
  for (const s of library) categories.set(s.category, [...(categories.get(s.category) ?? []), s]);
  return (
    <aside className="editor-palette" aria-label="Palette">
      <div className="palette-tools" role="toolbar" aria-label="Tools">
        {TOOLS.map((t) => (
          <button key={t.kind} type="button" className={tool.kind === t.kind ? "active" : ""} aria-pressed={tool.kind === t.kind}
                  onClick={() => onTool({ kind: t.kind } as Tool)}>
            {t.label}
          </button>
        ))}
      </div>
      {[...categories].map(([cat, syms]) => (
        <section key={cat} aria-label={cat}>
          <h3>{cat}</h3>
          <div className="palette-symbols">
            {syms.map((s) => (
              <button key={s.id} type="button" title={s.dis} aria-label={`Symbol ${s.dis}`}
                      className={tool.kind === "symbol" && tool.symbol === s.id ? "active" : ""}
                      aria-pressed={tool.kind === "symbol" && tool.symbol === s.id}
                      onClick={() => onTool({ kind: "symbol", symbol: s.id })}>
                <SymbolIcon sym={s} />
                <span>{s.dis}</span>
              </button>
            ))}
          </div>
        </section>
      ))}
    </aside>
  );
}

/** A symbol drawn small, for lists (the On group shown for site symbols when `on`). */
export function SymbolIcon({ sym, on = false, size = 36 }: { sym: LibrarySymbol; on?: boolean; size?: number }) {
  const [art, setArt] = useState("");
  useEffect(() => {
    if (sym.svg) return;
    let live = true;
    void symbolSvg(sym.id).then((a) => live && setArt(a));
    return () => { live = false; };
  }, [sym.id, sym.svg]);
  const parts = sym.svg ? svgParts(sym.svg) : { inner: art, viewBox: "0 0 64 64" };
  return (
    <svg className={`symbol-icon${on ? " running" : ""}`} width={size} height={size} viewBox={parts.viewBox}
         aria-hidden="true" dangerouslySetInnerHTML={{ __html: parts.inner }} />
  );
}
