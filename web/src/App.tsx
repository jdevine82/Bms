import { useCallback, useEffect, useState } from "react";
import type { LiveValue, Point, Site } from "./api";
import { getPoints, getSite } from "./api";
import { useLive } from "./live";
import { AccountPage } from "./auth/AccountPage";
import { useAuth } from "./auth/AuthGate";
import { logout } from "./auth/authApi";
import { PointsPage } from "./PointsPage";
import { useHash } from "./setup/nav";
import { SetupApp } from "./setup/SetupApp";
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
  const hash = useHash();
  const { user, can, refresh } = useAuth();
  const inSetup = hash.page === "setup" && can("admin");
  const inAccount = hash.page === "account";
  const go = (p: Page) => {
    if (inSetup || inAccount) history.pushState(null, "", location.pathname + location.search);
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
          <button type="button" className={!inSetup && !inAccount && page === "points" ? "active" : ""} onClick={() => go("points")}>
            Points
          </button>
          <button type="button" className={!inSetup && !inAccount && page === "trends" ? "active" : ""} onClick={() => go("trends")}>
            Trends
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
      <main className={inSetup ? "wide" : ""}>
        {error && <p className="error">{error}</p>}
        {inSetup && <SetupApp />}
        {inAccount && (
          <div className="setup plain">
            <AccountPage />
          </div>
        )}
        {site && !inSetup && !inAccount && page === "points" && <PointsPage site={site} points={points} live={live} onTrend={openTrend} />}
        {site && !inSetup && !inAccount && page === "trends" && (
          <TrendPage site={site} points={points} selected={selected} onSelect={setSelected} />
        )}
      </main>
    </div>
  );
}
