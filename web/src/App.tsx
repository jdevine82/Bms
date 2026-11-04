import { useCallback, useEffect, useState } from "react";
import type { LiveValue, Point, Site } from "./api";
import { getPoints, getSite } from "./api";
import { useLive } from "./live";
import { PointsPage } from "./PointsPage";
import { TrendPage } from "./TrendPage";

type Page = "points" | "trends";
type Theme = "light" | "dark" | "system";

function readTheme(): Theme {
  try {
    const saved = localStorage.getItem("bms-theme");
    return saved === "light" || saved === "dark" ? saved : "system";
  } catch {
    return "system";
  }
}

export function App() {
  const [site, setSite] = useState<Site | null>(null);
  const [points, setPoints] = useState<Point[]>([]);
  const [live, setLive] = useState<Record<string, LiveValue>>({});
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState<Page>("points");
  const [selected, setSelected] = useState<string[]>([]);
  const [theme, setTheme] = useState<Theme>(readTheme);

  useEffect(() => {
    Promise.all([getSite(), getPoints()])
      .then(([s, p]) => {
        setSite(s);
        setPoints(p);
        document.title = `${s.dis} · Devine BMS`;
      })
      .catch((e: unknown) => setError(`Cannot reach the BMS backend: ${String(e)}`));
  }, []);

  useEffect(() => {
    if (theme === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("bms-theme", theme);
    } catch {
      /* storage may be unavailable; the theme still applies for this visit */
    }
  }, [theme]);

  const onValue = useCallback((v: LiveValue) => setLive((old) => ({ ...old, [v.id]: v })), []);
  const connected = useLive(points.length ? "all" : [], onValue);

  const openTrend = (id: string) => {
    setSelected((s) => (s.includes(id) ? s : [...s, id].slice(-4)));
    setPage("trends");
  };

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="site-name">{site?.dis ?? "Devine BMS"}</span>
          <span className={`conn ${connected ? "on" : "off"}`}>{connected ? "Live" : "Connecting…"}</span>
        </div>
        <nav aria-label="Pages">
          <button type="button" className={page === "points" ? "active" : ""} onClick={() => setPage("points")}>
            Points
          </button>
          <button type="button" className={page === "trends" ? "active" : ""} onClick={() => setPage("trends")}>
            Trends
          </button>
        </nav>
        <label className="theme">
          <span className="visually-hidden">Theme</span>
          <select value={theme} onChange={(e) => setTheme(e.target.value as Theme)}>
            <option value="system">System theme</option>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </select>
        </label>
      </header>
      <main>
        {error && <p className="error">{error}</p>}
        {site && page === "points" && <PointsPage site={site} points={points} live={live} onTrend={openTrend} />}
        {site && page === "trends" && (
          <TrendPage site={site} points={points} selected={selected} onSelect={setSelected} />
        )}
      </main>
    </div>
  );
}
