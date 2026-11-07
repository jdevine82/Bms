// Standalone HMI screens (milestone 11a; main spec 6.5): the status strip's USB export status,
// Overrides (setpoints with their range, hand modes with the time left and Back to Auto) and the
// Log (temperatures and power from the RAM log, USB export, Export now, Download CSV).
import { useCallback, useEffect, useMemo, useState } from "react";
import type { LiveValue, Point, Site } from "../api";
import { getHistory } from "../api";
import { useAuth } from "../auth/AuthGate";
import { listOverrides, releaseToAuto } from "../commands/commandsApi";
import { get, post } from "../setup/api";
import { SetupError } from "../setup/api";
import { Chart, toSamples, type Sample } from "../TrendPage";
import { HmiSetpoints, PinPad } from "./HmiSetpoints";

export interface UsbStatus {
  enabled: boolean;
  intervalMin: number;
  lastExport: string | null;
  lastResult: string | null;
  lastRows: number;
  nextExport: string | null;
}
export interface StandaloneStatus {
  mode: "standalone";
  settings: { logWindowH: number; usbEnabled: boolean; usbIntervalMin: number };
  usb: UsbStatus;
  state: { file: string; lastSaved: string | null; lastError: string | null; saves: number };
  points: number;
  pointLimit: number;
  loggedPoints: number;
}

export const getStandalone = () => get<StandaloneStatus>("/api/standalone/status");

export function useStandaloneStatus(enabled: boolean, every = 30000) {
  const [status, setStatus] = useState<StandaloneStatus | null>(null);
  const refresh = useCallback(() => getStandalone().then(setStatus).catch(() => undefined), []);
  useEffect(() => {
    if (!enabled) return;
    void refresh();
    const timer = window.setInterval(refresh, every);
    return () => window.clearInterval(timer);
  }, [refresh, every, enabled]);
  return { status, refresh };
}

const time = (iso: string | null, tz: string) =>
  iso ? new Date(iso).toLocaleString([], { timeZone: tz, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "never";

/** The USB export in a few words, for the status strip. */
export function usbText(u: UsbStatus | undefined, tz: string): { text: string; bad: boolean } {
  if (!u || !u.enabled) return { text: "USB export off", bad: false };
  if (u.lastResult && u.lastResult !== "ok") return { text: `USB: ${u.lastResult}`, bad: true };
  if (!u.lastExport) return { text: "USB: not yet", bad: false };
  const at = new Date(u.lastExport);
  const today = at.toLocaleDateString([], { timeZone: tz }) === new Date().toLocaleDateString([], { timeZone: tz });
  return { text: `USB ${today ? at.toLocaleTimeString([], { timeZone: tz, hour: "2-digit", minute: "2-digit" }) : time(u.lastExport, tz)}`, bad: false };
}

export function HmiOverrides({ points, live, tz }: { points: Point[]; live: Record<string, LiveValue>; tz: string }) {
  const { user, can, refresh } = useAuth();
  const unlocked = !!user.username && can("operator");
  const [hands, setHands] = useState<{ pointId: string; point: string; dis: string; value: unknown; endsAt: string; setBy: string }[]>([]);
  const [unlocking, setUnlocking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());
  const load = useCallback(() => listOverrides().then(setHands).catch(() => undefined), []);
  useEffect(() => {
    void load();
    const on = () => void load();
    window.addEventListener("bms-override", on);
    const tick = window.setInterval(() => setNow(Date.now()), 30000);
    return () => { window.removeEventListener("bms-override", on); window.clearInterval(tick); };
  }, [load]);

  if (unlocking && !unlocked) return <PinPad onDone={async () => { setUnlocking(false); await refresh(); }} />;
  const left = (iso: string) => {
    const m = Math.max(0, Math.round((Date.parse(iso) - now) / 60000));
    return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min left` : `${m} min left`;
  };
  return (
    <div className="hmi-overrides">
      <section aria-label="Hand modes" className="hmi-hands">
        <h2>Hand modes</h2>
        {hands.length === 0 && <p className="hmi-note">Nothing is in hand mode. Everything runs in Auto.</p>}
        <ul>
          {hands.map((h) => (
            <li key={h.pointId}>
              <span><strong>{h.dis}</strong> · {String(h.value === true ? "On" : h.value === false ? "Off" : h.value)}
                <span className="hmi-small"> · {left(h.endsAt)} · set by {h.setBy}</span></span>
              <button type="button" onClick={async () => {
                if (!unlocked) { setUnlocking(true); return; }
                try { await releaseToAuto(h.pointId); setError(null); await load(); }
                catch (e) { setError(e instanceof SetupError ? e.message : String(e)); }
              }}>Back to Auto</button>
            </li>
          ))}
        </ul>
        {error && <p className="hmi-pin-error" role="alert">{error}</p>}
        <p className="hmi-small">After a power cut, hand modes return to Auto.</p>
      </section>
      <section aria-label="Setpoints">
        <h2>Setpoints</h2>
        <HmiSetpoints points={points} live={live} tz={tz} />
      </section>
    </div>
  );
}

const RANGES = [1, 6, 24, 72];

export function HmiLog({ site, points, status, onExported }: {
  site: Site; points: Point[]; status: StandaloneStatus | null; onExported: () => void;
}) {
  const groups = useMemo(() => {
    const logged = points.filter((p) => p.his && p.kind === "Number");
    return {
      Temperatures: logged.filter((p) => "temp" in p.tags).slice(0, 4),
      Power: logged.filter((p) => "power" in p.tags).slice(0, 4),
    };
  }, [points]);
  const [group, setGroup] = useState<"Temperatures" | "Power">(groups.Temperatures.length ? "Temperatures" : "Power");
  const [hours, setHours] = useState(24);
  const [samples, setSamples] = useState<Record<string, Sample[]>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const series = groups[group];

  useEffect(() => {
    let cancelled = false;
    const end = new Date();
    const start = new Date(end.getTime() - hours * 3600e3);
    Promise.all(series.map((p) => getHistory(p.id, start, end)))
      .then((all) => { if (!cancelled) setSamples(Object.fromEntries(all.map((h) => [h.id, toSamples(h.rows)]))); })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [series, hours]);

  const usb = status?.usb;
  const windowH = status?.settings.logWindowH ?? 72;
  return (
    <div className="hmi-log">
      <div className="hmi-log-head">
        <div className="hmi-ranges" role="group" aria-label="Log">
          {(["Temperatures", "Power"] as const).map((g) => (
            <button key={g} type="button" className={group === g ? "active" : ""} aria-pressed={group === g}
                    disabled={!groups[g].length} onClick={() => setGroup(g)}>{g}</button>
          ))}
        </div>
        <div className="hmi-ranges" role="group" aria-label="Range">
          {RANGES.filter((h) => h <= windowH).map((h) => (
            <button key={h} type="button" className={hours === h ? "active" : ""} aria-pressed={hours === h} onClick={() => setHours(h)}>
              {h} h</button>
          ))}
        </div>
      </div>
      {series.length ? <Chart site={site} series={series} samples={samples} height={250} />
        : <p className="hmi-note">No logged {group.toLowerCase()} points.</p>}
      <div className="hmi-usb" aria-label="USB export">
        <span className={usb && usb.lastResult && usb.lastResult !== "ok" ? "bad" : ""}>
          {usb?.lastExport ? `Last USB export ${time(usb.lastExport, site.ianaTz)}` : usbText(usb, site.ianaTz).text}{usb?.nextExport ? ` · next ${time(usb.nextExport, site.ianaTz)}` : ""}
        </span>
        <button type="button" disabled={busy || !usb?.enabled} onClick={async () => {
          setBusy(true);
          try {
            const r = await post<UsbStatus>("/api/standalone/export", {});
            setMessage(r.lastResult === "ok" ? `Exported ${r.lastRows} minutes to the USB stick.` : `Export failed: ${r.lastResult}`);
            onExported();
          } catch (e) { setMessage(String(e)); } finally { setBusy(false); }
        }}>Export now</button>
        <a className="hmi-button" href={`/api/standalone/log.csv?hours=${hours}`} download>Download CSV</a>
      </div>
      {message && <p className="hmi-small" role="status">{message}</p>}
      <p className="hmi-small">The log is kept in memory for {windowH} hours, one sample a minute; without the USB export it is lost when the power goes off.</p>
    </div>
  );
}
