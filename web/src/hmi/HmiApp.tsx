// HMI layout for the Pi touch screen (milestone 3 spec section 4, main spec 6.2 and 6.3).
// Dark only, five bottom tabs, touch targets of at least 44 px. Works at 1024 x 600 and 1280 x 720.

import { useCallback, useEffect, useMemo, useState } from "react";
import type { LiveValue, Point, Site } from "../api";
import { getHistory, getPoints, getSite } from "../api";
import { STATUS_TEXT, formatValue } from "../format";
import { useLive } from "../live";
import { HmiAlarms } from "./HmiAlarms";
import { HmiSetpoints } from "./HmiSetpoints";
import { useAuth } from "../auth/AuthGate";
import { Chart, toSamples, type Sample } from "../TrendPage";
import "./hmi.css";

type Tab = "overview" | "graphics" | "setpoints" | "schedules" | "trends";
const TABS: { key: Tab; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "graphics", label: "Graphics" },
  { key: "setpoints", label: "Setpoints" },
  { key: "schedules", label: "Schedules" },
  { key: "trends", label: "Trends" },
];
const OFFLINE_AFTER_MS = 5000;

export function HmiApp() {
  const { user, can } = useAuth();
  const [site, setSite] = useState<Site | null>(null);
  const [points, setPoints] = useState<Point[]>([]);
  const [live, setLive] = useState<Record<string, LiveValue>>({});
  const [tab, setTab] = useState<Tab>("overview");
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    document.documentElement.dataset.theme = "dark";
    document.documentElement.classList.add("hmi-root");
    let stop = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const load = () =>
      Promise.all([getSite(), getPoints()])
        .then(([s, p]) => {
          if (stop) return;
          setSite(s);
          setPoints(p);
          setLoadError(false);
        })
        .catch(() => {
          if (stop) return;
          setLoadError(true);
          retry = setTimeout(load, 3000); // keep trying; the HMI must recover by itself
        });
    load();
    return () => {
      stop = true;
      clearTimeout(retry);
    };
  }, []);

  const onValue = useCallback((v: LiveValue) => setLive((old) => ({ ...old, [v.id]: v })), []);
  useEffect(() => {
    const on = (e: Event) => {
      const { pointId, override } = (e as CustomEvent).detail as { pointId: string; override: Point["override"] };
      setPoints((old) => old.map((p) => (p.id === pointId ? { ...p, override } : p)));
    };
    window.addEventListener("bms-override", on);
    return () => window.removeEventListener("bms-override", on);
  }, []);

  const connected = useLive(points.length ? "all" : [], onValue);
  const offline = useOffline(connected, points.length > 0);

  return (
    <div className={`hmi ${offline ? "hmi-offline" : ""}`}>
      <header className="hmi-top">
        <span className="hmi-site">{site?.dis ?? "Devine BMS"}</span>
        <span className={`hmi-conn ${offline || loadError ? "bad" : "ok"}`}>
          {offline || loadError ? "Server not reachable" : "Connected"}
        </span>
        <span className="hmi-spacer" />
        {site && <Clock timeZone={site.ianaTz} />}
        <span className="hmi-lock">{user.username && can("operator") ? `Unlocked: ${user.dis}` : "Locked"}</span>
      </header>

      {(offline || loadError) && (
        <div className="hmi-banner" role="alert">
          The site server cannot be reached. Values are not live. Reconnecting…
        </div>
      )}

      <main className="hmi-main">
        {!site && !loadError && <p className="hmi-note">Loading…</p>}
        {site && tab === "overview" && <HmiAlarms />}
        {site && tab === "overview" && <Overview points={points} live={live} />}
        {site && tab === "trends" && <ShortTrends site={site} points={points} />}
        {site && tab === "setpoints" && <HmiSetpoints points={points} live={live} tz={site.ianaTz} />}
        {site && (tab === "graphics" || tab === "schedules") && (
          <ComingLater tab={TABS.find((t) => t.key === tab)!.label} />
        )}
      </main>

      <nav className="hmi-tabs" aria-label="HMI screens">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            className={tab === t.key ? "active" : ""}
            aria-current={tab === t.key ? "page" : undefined}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </nav>
    </div>
  );
}

/** True when the live connection has been down for more than 5 seconds. */
function useOffline(connected: boolean, started: boolean): boolean {
  const [offline, setOffline] = useState(false);
  useEffect(() => {
    if (connected || !started) {
      setOffline(false);
      return;
    }
    const timer = setTimeout(() => setOffline(true), OFFLINE_AFTER_MS);
    return () => clearTimeout(timer);
  }, [connected, started]);
  return offline;
}

function Clock({ timeZone }: { timeZone: string }) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);
  const text = new Intl.DateTimeFormat(undefined, {
    timeZone,
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(now);
  return <span className="hmi-clock">{text}</span>;
}

interface Group {
  key: string;
  title: string;
  points: Point[];
}

function Overview({ points, live }: { points: Point[]; live: Record<string, LiveValue> }) {
  const groups = useMemo(() => {
    const byKey = new Map<string, Group>();
    for (const p of points) {
      const owner = p.equip ?? p.space;
      const key = owner ? owner.id : "other";
      if (!byKey.has(key)) byKey.set(key, { key, title: owner?.dis ?? "Other points", points: [] });
      byKey.get(key)!.points.push(p);
    }
    return [...byKey.values()];
  }, [points]);

  if (points.length === 0) return <p className="hmi-note">No points are set up yet.</p>;
  return (
    <div className="hmi-tiles">
      {groups.map((g) => {
        const bad = g.points.filter((p) => (live[p.id] ?? p.cur).status !== "ok").length;
        return (
          <section key={g.key} className="hmi-tile" aria-label={g.title}>
            <h2>
              {g.title}
              {bad > 0 && <span className="hmi-tile-flag">{bad} not Good</span>}
            </h2>
            <ul>
              {g.points.map((p) => {
                const cur = live[p.id] ?? p.cur;
                const good = cur.status === "ok";
                return (
                  <li key={p.id} className={good ? "" : "not-good"}>
                    <span className="hmi-point">{p.dis}</span>
                    <span className="hmi-value" data-testid={`hmi-value-${p.name}`}>
                      {formatValue(p, cur)}
                    </span>
                    {!good && (
                      <span className={`hmi-status status-${cur.status}`}>{STATUS_TEXT[cur.status] ?? cur.status}</span>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

const SHORT_RANGES = [
  { label: "1 hour", hours: 1 },
  { label: "6 hours", hours: 6 },
  { label: "24 hours", hours: 24 },
];

function ShortTrends({ site, points }: { site: Site; points: Point[] }) {
  const trendable = useMemo(() => points.filter((p) => p.his && p.kind === "Number"), [points]);
  const [pointId, setPointId] = useState<string | null>(null);
  const [hours, setHours] = useState(1);
  const [samples, setSamples] = useState<Record<string, Sample[]>>({});
  const [error, setError] = useState(false);
  const point = trendable.find((p) => p.id === pointId) ?? trendable[0];

  useEffect(() => {
    if (!point) return;
    let cancelled = false;
    const end = new Date();
    const start = new Date(end.getTime() - hours * 3600e3);
    getHistory(point.id, start, end)
      .then((h) => {
        if (cancelled) return;
        setSamples({ [h.id]: toSamples(h.rows) });
        setError(false);
      })
      .catch(() => !cancelled && setError(true));
    return () => {
      cancelled = true;
    };
  }, [point, hours]);

  if (!point) return <p className="hmi-note">No logged points to trend yet.</p>;
  return (
    <div className="hmi-trends">
      <div className="hmi-trend-points" role="group" aria-label="Point">
        {trendable.map((p) => (
          <button
            key={p.id}
            type="button"
            className={p.id === point.id ? "active" : ""}
            aria-pressed={p.id === point.id}
            onClick={() => setPointId(p.id)}
          >
            {p.dis}
          </button>
        ))}
      </div>
      <div className="hmi-trend-chart">
        <div className="hmi-ranges" role="group" aria-label="Range">
          {SHORT_RANGES.map((r) => (
            <button
              key={r.hours}
              type="button"
              className={hours === r.hours ? "active" : ""}
              aria-pressed={hours === r.hours}
              onClick={() => setHours(r.hours)}
            >
              {r.label}
            </button>
          ))}
        </div>
        {error ? (
          <p className="hmi-note">History cannot be loaded while the server is not reachable.</p>
        ) : (
          <Chart site={site} series={[point]} samples={samples} height={260} />
        )}
        <p className="hmi-small">Gaps show where the value was Bad or Stale. Longer history is in the web app.</p>
      </div>
    </div>
  );
}

function ComingLater({ tab }: { tab: string }) {
  return (
    <div className="hmi-later">
      <h2>{tab}</h2>
      <p>This screen arrives in a later milestone.</p>
    </div>
  );
}
