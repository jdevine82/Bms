// One live graphic (main spec 6.4, milestone 9A): SVG in the page, CSS animation, no scripts in
// symbols. States in priority order: Offline, Fault, Manual, then On or Off. Colour is never the
// only signal: Offline is dashed, Fault flashes with a badge, Manual has a hand badge, On animates,
// and every symbol says its state in words.
import { useEffect, useRef, useState } from "react";
import type { LiveValue, Point } from "../api";
import { formatValue } from "../format";
import type { Colour, ColourRule, Graphic, GraphicItem, LibrarySymbol, ValueBand } from "./graphicsApi";
import { svgParts, symbolSvg } from "./graphicsApi";
import "./graphics.css";

export type SymbolState = "offline" | "fault" | "manual" | "on" | "off";

const STATE_TEXT: Record<SymbolState, string> = { offline: "Offline", fault: "Fault", manual: "Hand", on: "On", off: "Off" };
const colourVar = (c: Colour) => `var(--g-${c})`;

interface Props {
  graphic: Graphic;
  library: Record<string, LibrarySymbol>;
  points: Record<string, Point>; // by name
  live: Record<string, LiveValue>; // by point id
  alarmed: Set<string>; // ids of points with an active alarm
  onCommand?: (p: Point) => void;
  onRoom?: (space: string) => void; // tapping a room opens its tab (when it has one)
  spaces?: Record<string, string>; // room name -> display name
}

/** Picks a value band, staying in the previous one until the value is `h` past the boundary. */
export function pickBand(value: number, bands: ValueBand[], h: number, prev: number | undefined): number {
  let i = bands.findIndex((b) => b.below === undefined || b.below === null || value < b.below);
  if (i < 0) i = bands.length - 1;
  if (prev !== undefined && prev < bands.length && i !== prev && h > 0) {
    const up = bands[prev].below;
    const down = prev > 0 ? bands[prev - 1].below : undefined;
    if (i > prev && up != null && value < up + h) return prev;
    if (i < prev && down != null && value >= down - h) return prev;
  }
  return i;
}

export function GraphicView({ graphic, library, points, live, alarmed, onCommand, onRoom, spaces = {} }: Props) {
  const bandMemory = useRef(new Map<string, number>());
  const w = graphic.width ?? 1000;
  const h = graphic.height ?? 600;

  const lv = (p: Point | undefined): LiveValue | undefined => (p ? live[p.id] ?? p.cur : undefined);
  const pt = (name: string | undefined) => (name ? points[name] : undefined);
  const num = (name: string): number | null => {
    const v = lv(pt(name));
    return v && v.status === "ok" && typeof v.value === "number" ? v.value : null;
  };

  function symbolState(item: Extract<GraphicItem, { type: "symbol" }>): { state: SymbolState; running: boolean } {
    const b = item.bind ?? {};
    const bound = [b.run, b.value, b.fault, b.command].map(pt).filter((p): p is Point => !!p);
    const run = lv(pt(b.run));
    const running = run?.status === "ok" && (run.value === true || (typeof run.value === "number" && run.value > 0));
    if (bound.length && bound.some((p) => lv(p)?.status !== "ok")) return { state: "offline", running: false };
    const fault = lv(pt(b.fault));
    if ((fault?.status === "ok" && fault.value === true) || bound.some((p) => alarmed.has(p.id)))
      return { state: "fault", running };
    if (pt(b.command)?.override) return { state: "manual", running };
    return { state: running ? "on" : "off", running };
  }

  function band(id: string, rule: { point: string; bands: ValueBand[]; hysteresis?: number }): ValueBand | null {
    const v = num(rule.point);
    if (v === null) return null;
    const i = pickBand(v, rule.bands, rule.hysteresis ?? 0, bandMemory.current.get(id));
    bandMemory.current.set(id, i);
    return rule.bands[i];
  }

  const symbols = new Map(graphic.items.filter((i) => i.type === "symbol").map((i) => [i.id, i]));

  function lineLook(id: string, rule: ColourRule | undefined):
    { colour: string; flow: boolean; noData: boolean; fill?: number } {
    if (!rule) return { colour: colourVar("grey"), flow: false, noData: false };
    if (rule.type === "follows") {
      const s = symbols.get(rule.item);
      if (!s || s.type !== "symbol") return { colour: colourVar("grey"), flow: false, noData: true };
      const { state, running } = symbolState(s);
      if (state === "offline") return { colour: colourVar("grey"), flow: false, noData: true };
      return { colour: colourVar(running ? rule.colour ?? "green" : "grey"), flow: running, noData: false };
    }
    if (rule.type === "bands") {
      const b = band(id, rule);
      if (!b) return { colour: colourVar("grey"), flow: false, noData: true };
      return { colour: colourVar(b.colour), flow: b.flow !== false, noData: false };
    }
    const v = num(rule.point);
    if (v === null) return { colour: colourVar("grey"), flow: false, noData: true };
    const min = rule.min ?? 0;
    const max = rule.max ?? 100;
    const fill = Math.max(0, Math.min(100, ((v - min) / (max - min)) * 100));
    return { colour: colourVar(rule.colour ?? "blue"), flow: fill > 0, noData: false, fill };
  }

  const valueText = (name: string) => {
    const p = pt(name);
    if (!p) return "--";
    const v = lv(p)!;
    const text = formatValue(p, v);
    return text === "--" && p.unit ? `-- ${p.unit}` : text;
  };

  return (
    <svg className="graphic" viewBox={`0 0 ${w} ${h}`} role="group" aria-label={graphic.dis}
         style={{ aspectRatio: `${w} / ${h}` }}>
      {graphic.background && <Background bg={graphic.background} />}
      {graphic.items.map((item) => {
        switch (item.type) {
          case "text":
            return <text key={item.id} data-id={item.id} className="g-text" x={item.x} y={item.y} fontSize={item.size ?? 16}>{item.text}</text>;
          case "value": {
            const p = pt(item.point);
            return (
              <text key={item.id} data-id={item.id} className="g-value" x={item.x} y={item.y} textAnchor="middle"
                    data-status={p ? lv(p)?.status : "missing"}>
                {item.label && <tspan className="g-muted">{item.label} </tspan>}
                <tspan>{valueText(item.point)}</tspan>
              </text>
            );
          }
          case "pipe":
          case "duct": {
            const look = lineLook(item.id, item.rule);
            const pts = item.path.map(([x, y]) => `${x},${y}`).join(" ");
            const width = item.width ?? (item.type === "duct" ? 14 : 6);
            const base = look.fill !== undefined ? colourVar("grey") : look.colour;
            return (
              <g key={item.id} className={`g-line g-${item.type}${look.flow ? " flowing" : ""}${look.noData ? " no-data" : ""}`}
                 data-id={item.id} data-flow={look.flow ? "yes" : "no"}>
                <polyline className="g-line-base" points={pts} stroke={base} strokeWidth={width} />
                {look.fill !== undefined && (
                  <polyline className="g-line-fill" points={pts} stroke={look.colour} strokeWidth={width}
                            pathLength={100} strokeDasharray={`${look.fill} 100`} />
                )}
                {look.flow && <polyline className="g-flow" points={pts} strokeWidth={Math.max(2, width / 3)} />}
              </g>
            );
          }
          case "room": {
            const b = item.rule ? band(item.id, item.rule) : null;
            const colour = b ? colourVar(b.colour) : "var(--border)";
            const open = onRoom ? () => onRoom(item.space) : undefined;
            const name = spaces[item.space] ?? item.space;
            return (
              <g key={item.id} className={`g-room${open ? " tappable" : ""}`} data-id={item.id}
                 role={open ? "button" : undefined} tabIndex={open ? 0 : undefined}
                 aria-label={open ? `Open ${name}` : undefined} onClick={open}
                 onKeyDown={open ? (e) => (e.key === "Enter" || e.key === " ") && open() : undefined}>
                <rect x={item.x} y={item.y} width={item.w} height={item.h} rx={6} stroke={colour}
                      fill={colour} className={b ? "banded" : ""} />
                <text className="g-room-name" x={item.x + 12} y={item.y + 24}>{spaces[item.space] ?? item.space}</text>
                {(item.values ?? []).map((v, i) => (
                  <text key={v} className="g-room-value" x={item.x + 12} y={item.y + 52 + i * 22}>{valueText(v)}</text>
                ))}
              </g>
            );
          }
          case "symbol":
            return (
              <SymbolView key={item.id} item={item} lib={library[item.symbol]} {...symbolState(item)}
                          valueText={item.bind?.value ? valueText(item.bind.value) : null}
                          level={item.bind?.value ? num(item.bind.value) : null}
                          command={pt(item.bind?.command)} onCommand={onCommand} />
            );
        }
        return null;
      })}
    </svg>
  );
}

function Background({ bg }: { bg: NonNullable<Graphic["background"]> }) {
  const { inner, viewBox } = svgParts(bg.svg); // cleaned on the server (milestone 9B)
  return (
    <svg className="g-background" x={bg.x ?? 0} y={bg.y ?? 0} width={bg.w} height={bg.h} viewBox={viewBox}
         opacity={bg.opacity ?? 1} preserveAspectRatio="xMidYMid meet" aria-hidden="true"
         dangerouslySetInnerHTML={{ __html: inner }} />
  );
}

function SymbolView({ item, lib, state, running, valueText, level, command, onCommand }: {
  item: Extract<GraphicItem, { type: "symbol" }>;
  lib: LibrarySymbol | undefined;
  state: SymbolState;
  running: boolean;
  valueText: string | null;
  level: number | null;
  command: Point | undefined;
  onCommand?: (p: Point) => void;
}) {
  const [fileArt, setFileArt] = useState("");
  const site = lib?.svg ? svgParts(lib.svg) : null; // a site symbol carries its own (cleaned) SVG
  useEffect(() => {
    if (lib?.svg) return;
    let live = true;
    void symbolSvg(item.symbol).then((s) => live && setFileArt(s));
    return () => {
      live = false;
    };
  }, [item.symbol, lib?.svg]);
  const art = site ? site.inner : fileArt;

  const size = item.size ?? 64;
  const k = size / 64;
  const tap = command && onCommand ? () => onCommand(command) : undefined;
  const spinning = running && state !== "offline";
  const kind = lib?.kind ?? "state";
  // Only a symbol bound to a state says it; an unbound or value-only one has nothing to say.
  const stateWord = item.bind?.run || item.bind?.fault || item.bind?.command ? STATE_TEXT[state] : null;
  const words = [stateWord, valueText].filter(Boolean).join(" · ");
  const label = item.label ?? lib?.dis ?? item.symbol;

  return (
    <g className={`g-symbol s-${state}${spinning ? " spinning running" : ""}${tap ? " tappable" : ""}${
         lib?.animation === "flash" ? " flash-on" : ""}`}
       transform={`translate(${item.x} ${item.y})`} data-id={item.id} data-state={state}
       role={tap ? "button" : "img"} tabIndex={tap ? 0 : undefined}
       aria-label={`${label}${words ? `: ${words}` : ""}${tap ? ". Tap to command" : ""}`}
       onClick={tap} onKeyDown={tap ? (e) => (e.key === "Enter" || e.key === " ") && tap() : undefined}>
      <rect className="g-hit" x={-6} y={-6} width={size + 12} height={size + 12} rx={8} />
      <g transform={item.rotate ? `rotate(${item.rotate} ${size / 2} ${size / 2})` : undefined}>
        <g className={`g-art${lib?.spinWhole || lib?.animation === "spin" ? " g-spin-whole" : ""}`}>
          <svg width={size} height={size} viewBox={site?.viewBox ?? "0 0 64 64"} dangerouslySetInnerHTML={{ __html: art }} />
        </g>
        {lib?.spin && (
          <g transform={`scale(${k})`}>
            <g className="g-rotor">
              <path d={rotor(lib.spin.cx, lib.spin.cy, lib.spin.r)} />
            </g>
          </g>
        )}
        {kind === "level" && level !== null && (
          <rect className="g-level" x={size * 0.36} width={size * 0.28}
                y={size * (0.94 - 0.82 * clamp(level / 100))} height={size * 0.82 * clamp(level / 100)} />
        )}
      </g>
      {state === "offline" && <rect className="g-offline" x={-4} y={-4} width={size + 8} height={size + 8} rx={6} />}
      {state === "fault" && (
        <g className="g-badge g-badge-fault" transform={`translate(${size - 2} 2)`} aria-hidden="true">
          <circle r={9} /><text y={4} textAnchor="middle">!</text>
        </g>
      )}
      {(state === "manual" || (command?.override && state === "fault")) && (
        <g className="g-badge g-badge-hand" transform="translate(2 2)" aria-hidden="true">
          <circle r={9} /><text y={4} textAnchor="middle">✋</text>
        </g>
      )}
      <text className="g-label" x={size / 2} y={size + 16} textAnchor="middle">{label}</text>
      {words && <text className="g-state" x={size / 2} y={size + 33} textAnchor="middle">{words}</text>}
    </g>
  );
}

const clamp = (x: number) => Math.max(0, Math.min(1, x));

/** Two blades through the centre: symmetric, so it spins about its own middle. */
function rotor(cx: number, cy: number, r: number): string {
  const a = r * 0.8 * Math.SQRT1_2;
  return `M${cx - a} ${cy - a} L${cx + a} ${cy + a} M${cx + a} ${cy - a} L${cx - a} ${cy + a}`;
}
