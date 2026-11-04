import { useEffect, useMemo, useRef, useState } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import type { HisRow, LiveValue, Point, Site } from "./api";
import { getHistory, historyCsvUrl } from "./api";
import { useLive } from "./live";

const MAX_SERIES = 4;
const RANGES = [
  { key: "1h", label: "Last hour", ms: 3600e3 },
  { key: "24h", label: "24 hours", ms: 24 * 3600e3 },
  { key: "7d", label: "7 days", ms: 7 * 24 * 3600e3 },
  { key: "30d", label: "30 days", ms: 30 * 24 * 3600e3 },
  { key: "custom", label: "Custom", ms: 0 },
] as const;
type RangeKey = (typeof RANGES)[number]["key"];

/** [unix seconds, value or null]. null is a gap (Bad or Stale), never joined across. */
type Sample = [number, number | null];

interface Props {
  site: Site;
  points: Point[];
  selected: string[];
  onSelect: (ids: string[]) => void;
}

function toSamples(rows: HisRow[]): Sample[] {
  return rows.map((r) => [Date.parse(r.ts) / 1000, r.status === "ok" && typeof r.value === "number" ? r.value : null]);
}

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function localInput(date: Date): string {
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60e3);
  return shifted.toISOString().slice(0, 16);
}

/** Trend chart for up to four points (milestone 2 spec 8). */
export function TrendPage({ site, points, selected, onSelect }: Props) {
  const [range, setRange] = useState<RangeKey>("1h");
  const [customStart, setCustomStart] = useState(() => localInput(new Date(Date.now() - 3600e3)));
  const [customEnd, setCustomEnd] = useState(() => localInput(new Date()));
  const [samples, setSamples] = useState<Record<string, Sample[]>>({});
  const [window_, setWindow] = useState<{ start: Date; end: Date } | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const trendable = useMemo(() => points.filter((p) => p.his && p.kind === "Number"), [points]);
  const chosen = useMemo(
    () => selected.map((id) => trendable.find((p) => p.id === id)).filter((p): p is Point => Boolean(p)),
    [selected, trendable],
  );
  const live = range !== "custom";

  // Load history whenever the points or the range change.
  useEffect(() => {
    if (chosen.length === 0) {
      setSamples({});
      return;
    }
    let start: Date;
    let end: Date;
    if (range === "custom") {
      start = new Date(customStart);
      end = new Date(customEnd);
      if (!(end > start)) {
        setError("The end of the range must be after the start.");
        return;
      }
    } else {
      end = new Date();
      start = new Date(end.getTime() - RANGES.find((r) => r.key === range)!.ms);
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all(chosen.map((p) => getHistory(p.id, start, end)))
      .then((results) => {
        if (cancelled) return;
        setSamples(Object.fromEntries(results.map((h) => [h.id, toSamples(h.rows)])));
        setNotes(
          results
            .filter((h) => h.thinned)
            .map((h) => `${h.name}: ${h.rowCount.toLocaleString()} readings thinned to fit (minimum and maximum kept).`),
        );
        setWindow({ start, end });
      })
      .catch((e: unknown) => !cancelled && setError(String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [chosen, range, customStart, customEnd]);

  // Extend the chart live while a relative range is shown.
  useLive(live ? chosen.map((p) => p.id) : [], (v: LiveValue) => {
    const t = Date.parse(v.ts) / 1000;
    const value = v.status === "ok" && typeof v.value === "number" ? v.value : null;
    const span = RANGES.find((r) => r.key === range)!.ms / 1000;
    setSamples((old) => {
      const list = old[v.id];
      if (!list) return old;
      if (list.length && list[list.length - 1][0] >= t) return old; // snapshot or duplicate
      const kept = list.filter(([ts]) => ts >= t - span);
      return { ...old, [v.id]: [...kept, [t, value]] };
    });
  });

  const toggle = (id: string) => {
    if (selected.includes(id)) onSelect(selected.filter((s) => s !== id));
    else if (selected.length < MAX_SERIES) onSelect([...selected, id]);
  };

  return (
    <section>
      <div className="trend-layout">
        <aside className="picker" aria-label="Points to plot">
          <h2>Points</h2>
          <p className="muted small">Pick up to {MAX_SERIES}.</p>
          {trendable.map((p) => {
            const on = selected.includes(p.id);
            return (
              <label key={p.id} className={`pick ${on ? "on" : ""}`}>
                <input
                  type="checkbox"
                  checked={on}
                  disabled={!on && selected.length >= MAX_SERIES}
                  onChange={() => toggle(p.id)}
                />
                <span>
                  {p.dis}
                  {p.unit && <span className="muted"> ({p.unit})</span>}
                </span>
              </label>
            );
          })}
        </aside>
        <div className="chart-area">
          <div className="toolbar">
            <div className="segmented" role="group" aria-label="Range">
              {RANGES.map((r) => (
                <button
                  key={r.key}
                  type="button"
                  className={range === r.key ? "active" : ""}
                  aria-pressed={range === r.key}
                  onClick={() => setRange(r.key)}
                >
                  {r.label}
                </button>
              ))}
            </div>
            {range === "custom" && (
              <div className="custom-range">
                <label className="field">
                  <span>From (your browser's time)</span>
                  <input type="datetime-local" value={customStart} onChange={(e) => setCustomStart(e.target.value)} />
                </label>
                <label className="field">
                  <span>To</span>
                  <input type="datetime-local" value={customEnd} onChange={(e) => setCustomEnd(e.target.value)} />
                </label>
              </div>
            )}
          </div>
          {error && <p className="error">{error}</p>}
          {chosen.length === 0 ? (
            <p className="empty">Pick a point on the left to draw its trend.</p>
          ) : (
            <>
              <Chart site={site} series={chosen} samples={samples} />
              <div className="below-chart">
                <span className="muted small">
                  {loading ? "Loading…" : live ? "Updating live. " : ""}Times in {site.ianaTz}. Gaps show when a value
                  was Bad or Stale.
                </span>
                {window_ && (
                  <span className="downloads">
                    {chosen.map((p) => (
                      <a key={p.id} className="button" href={historyCsvUrl(p.id, window_.start, window_.end)} download>
                        Export CSV: {p.dis}
                      </a>
                    ))}
                  </span>
                )}
              </div>
              {notes.map((n) => (
                <p key={n} className="muted small">
                  {n}
                </p>
              ))}
            </>
          )}
        </div>
      </div>
    </section>
  );
}

function Chart({ site, series, samples }: { site: Site; series: Point[]; samples: Record<string, Sample[]> }) {
  const holder = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const shape = series.map((p) => `${p.id}:${p.unit}`).join("|");

  const data = useMemo((): uPlot.AlignedData => {
    // uPlot needs one shared time axis. A series has a value only at its own times:
    // `undefined` elsewhere (joined across), `null` at its own gaps (left as a gap).
    const times = new Set<number>();
    series.forEach((p) => (samples[p.id] ?? []).forEach(([t]) => times.add(t)));
    const xs = [...times].sort((a, b) => a - b);
    const columns = series.map((p) => {
      const byTime = new Map(samples[p.id] ?? []);
      return xs.map((t) => (byTime.has(t) ? byTime.get(t)! : undefined));
    });
    return [xs, ...columns] as unknown as uPlot.AlignedData;
  }, [series, samples]);

  // Build the chart when the set of series changes.
  useEffect(() => {
    const el = holder.current;
    if (!el) return;
    const units = [...new Set(series.map((p) => p.unit ?? ""))];
    const colours = [1, 2, 3, 4].map((i) => cssVar(`--series-${i}`));
    const axisColour = cssVar("--text-muted");
    const gridColour = cssVar("--grid");
    const options: uPlot.Options = {
      width: el.clientWidth,
      height: 360,
      tzDate: (ts) => uPlot.tzDate(new Date(ts * 1e3), site.ianaTz),
      scales: Object.fromEntries(units.map((u) => [u || "none", { auto: true }])),
      axes: [
        { stroke: axisColour, grid: { stroke: gridColour } },
        ...units.map((u, i) => ({
          scale: u || "none",
          label: u || undefined,
          side: i % 2 === 0 ? 3 : 1,
          stroke: axisColour,
          grid: { show: i === 0, stroke: gridColour },
          size: 60,
        })),
      ],
      series: [
        { label: "Time" },
        ...series.map((p, i) => ({
          label: p.unit ? `${p.dis} (${p.unit})` : p.dis,
          scale: p.unit || "none",
          stroke: colours[i],
          width: 2,
          spanGaps: false,
          value: (_: uPlot, v: number | null) => (v == null ? "--" : v.toLocaleString(undefined, { maximumFractionDigits: 3 })),
        })),
      ],
      legend: { live: true },
      cursor: { drag: { x: true, y: false } },
    };
    plot.current = new uPlot(options, data, el);
    const resize = new ResizeObserver(() => plot.current?.setSize({ width: el.clientWidth, height: 360 }));
    resize.observe(el);
    return () => {
      resize.disconnect();
      plot.current?.destroy();
      plot.current = null;
    };
  }, [shape, site.ianaTz]); // rebuilt only when the series change; new data goes through setData below

  // New data without rebuilding.
  useEffect(() => {
    plot.current?.setData(data);
  }, [data]);

  return <div ref={holder} className="chart" data-testid="trend-chart" />;
}
