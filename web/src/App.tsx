import { Suspense, lazy, useCallback, useEffect, useState } from "react";
import type { LiveValue, Point, Site } from "./api";
import { getPoints, getSite } from "./api";
import { useLive } from "./live";
import { AlarmButton, AlarmsPage } from "./alarms/AlarmsPage";
import { SchedulesPage } from "./schedules/SchedulesPage";
import { GraphicsPage } from "./graphics/GraphicsPage";
import { AccountPage } from "./auth/AccountPage";
import { useAuth } from "./auth/AuthGate";
import { logout } from "./auth/authApi";
import { PointsPage } from "./PointsPage";
import { useHash } from "./setup/nav";
import { SetupApp } from "./setup/SetupApp";
import { TrendPage } from "./TrendPage";
import { HeldBanner } from "./hmi/HmiStandby";

type Page = "points" | "trends";
// The logic editor (React Flow) loads only when opened, so the main page and the HMI stay small.
const LogicPage = lazy(() => import("./logic/LogicPage").then((m) => ({ default: m.LogicPage })));
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
  const hash = useHash();
  const { user, can, refresh } = useAuth();
  const inSetup = hash.page === "setup" && can("admin");
  const inAccount = hash.page === "account";
  const inAlarms = hash.page === "alarms";
  const inSchedules = hash.page === "schedules";
  const inGraphics = hash.page === "graphics";
  const inLogic = hash.page === "logic";
  const elsewhere = inSetup || inAccount || inAlarms || inSchedules || inGraphics || inLogic;
  const go = (p: Page) => {
    if (elsewhere) history.pushState(null, "", location.pathname + location.search);
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    setPage(p);
  };
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

  // Hand-mode overrides change on the live connection (milestone 7A).
  useEffect(() => {
    const on = (e: Event) => {
      const { pointId, override } = (e as CustomEvent).detail as { pointId: string; override: Point["override"] };
      setPoints((old) => old.map((p) => (p.id === pointId ? { ...p, override } : p)));
    };
    window.addEventListener("bms-override", on);
    return () => window.removeEventListener("bms-override", on);
  }, []);

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
          <button type="button" className={inGraphics ? "active" : ""} onClick={() => { location.hash = "#/graphics"; }}>
            Graphics
          </button>
          <button type="button" className={!elsewhere && page === "points" ? "active" : ""} onClick={() => go("points")}>
            Points
          </button>
          <button type="button" className={!elsewhere && page === "trends" ? "active" : ""} onClick={() => go("trends")}>
            Trends
          </button>
          <AlarmButton active={inAlarms} onClick={() => { location.hash = "#/alarms"; }} />
          <button type="button" className={inSchedules ? "active" : ""} onClick={() => { location.hash = "#/schedules"; }}>
            Schedules
          </button>
          <button type="button" className={inLogic ? "active" : ""} onClick={() => { location.hash = "#/logic"; }}>
            Logic
          </button>
          {can("admin") && (
            <button
              type="button"
              className={inSetup ? "active" : ""}
              onClick={() => {
                location.hash = "#/setup";
              }}
            >
              Setup
            </button>
          )}
        </nav>
        <div className="user-menu">
          <a href="#/account" className="who" title="My account">
            {user.dis} · {user.role}
          </a>
          {user.username && (
            <button
              type="button"
              className="btn-plain"
              onClick={async () => {
                await logout();
                await refresh();
              }}
            >
              Sign out
            </button>
          )}
        </div>
        <label className="theme">
          <span className="visually-hidden">Theme</span>
          <select value={theme} onChange={(e) => setTheme(e.target.value as Theme)}>
            <option value="system">System theme</option>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </select>
        </label>
      </header>
      <HeldBanner />
      <main className={inSetup || inAlarms || inSchedules || inGraphics || inLogic ? "wide" : ""}>
        {error && <p className="error">{error}</p>}
        {inSetup && <SetupApp />}
        {inSchedules && (
          <div className="setup plain">
            <SchedulesPage />
          </div>
        )}
        {inAlarms && (
          <div className="setup plain">
            <AlarmsPage />
          </div>
        )}
        {inGraphics && site && (
          <div>
            <GraphicsPage site={site} points={points} live={live} selected={hash.section}
                          onSelect={(name) => { location.hash = `#/graphics/${encodeURIComponent(name)}`; }} />
          </div>
        )}
        {inLogic && (
          <Suspense fallback={<p className="muted">Loading…</p>}>
            <LogicPage open={hash.section ? decodeURIComponent(hash.section) : null} />
          </Suspense>
        )}
        {inAccount && (
          <div className="setup plain">
            <AccountPage />
          </div>
        )}
        {site && !elsewhere && page === "points" && <PointsPage site={site} points={points} live={live} onTrend={openTrend} />}
        {site && !elsewhere && page === "trends" && (
          <TrendPage site={site} points={points} selected={selected} onSelect={setSelected} />
        )}
      </main>
    </div>
  );
}
