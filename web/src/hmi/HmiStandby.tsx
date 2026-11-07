// HMI standby takeover and failback (milestone 11; main spec 9, decision 029).
// - The server's HMI page, opened by the kiosk with ?standby=http://localhost:8000: while the
//   server cannot be reached it asks the Pi; when the Pi has taken over it opens the Pi's HMI.
// - The Pi's own HMI: the direct-mode banner and the Server screen with Hand control back (PIN);
//   after handing back, and in standby, it returns to the server's HMI.
// - The web app: a banner while an HMI holds the site (the server polls nothing).
import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../auth/AuthGate";
import { post, SetupError } from "../setup/api";
import { PinPad } from "./HmiSetpoints";

export interface StandbyState {
  name: string;
  mode: "standby" | "direct";
  serverOk: boolean;
  serverUrl: string;
  serverHealthyS: number | null;
  serverLostS: number | null;
  takeoverAfterS: number;
  failbackAfterS: number;
  canFailback: boolean;
  tookOverAt: string | null;
  buses: { bus: string; kind: string; port: string; clear: boolean; heardAgoS: number | null; error: string | null }[];
  cacheRevision: string | null;
  note: string | null;
  auditPending: number;
}

const STANDBY_KEY = "bms-standby";

/** The Pi's own address, from ?standby= (remembered: the page may reload without it). */
export function standbyUrl(): string | null {
  const given = new URLSearchParams(location.search).get("standby");
  try {
    if (given) localStorage.setItem(STANDBY_KEY, given);
    return (given ?? localStorage.getItem(STANDBY_KEY))?.replace(/\/+$/, "") || null;
  } catch {
    return given?.replace(/\/+$/, "") || null;
  }
}

async function askPi(base: string): Promise<StandbyState | null> {
  try {
    const r = await fetch(`${base}/api/standby/state`, { cache: "no-store" });
    return r.ok ? ((await r.json()) as StandbyState) : null;
  } catch {
    return null;
  }
}

/** The server's HMI: while the server cannot be reached, ask the Pi; open its HMI once it has taken over. */
export function useTakeoverRedirect(offline: boolean): string | null {
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    const pi = standbyUrl();
    if (!offline || !pi) { setNote(null); return; }
    let stop = false;
    const check = async () => {
      const st = await askPi(pi);
      if (stop) return;
      if (st?.mode === "direct") { location.replace(`${pi}/hmi`); return; }
      if (st?.serverLostS != null) {
        setNote(st.note ?? `This HMI takes over if the server stays unreachable for ${st.takeoverAfterS} seconds.`);
      }
    };
    void check();
    const timer = window.setInterval(check, 3000);
    return () => { stop = true; window.clearInterval(timer); };
  }, [offline]);
  return note;
}

/** The Pi's standby state, every 2 seconds (the Pi's own HMI only). */
export function useStandbyState(enabled: boolean) {
  const [state, setState] = useState<StandbyState | null>(null);
  const refresh = useCallback(async () => { const st = await askPi(""); if (st) setState(st); }, []);
  useEffect(() => {
    if (!enabled) return;
    void refresh();
    const timer = window.setInterval(refresh, 2000);
    return () => window.clearInterval(timer);
  }, [enabled, refresh]);
  // Standby with the server answering: this screen belongs on the server's HMI.
  useEffect(() => {
    if (state?.mode === "standby" && state.serverOk && state.serverUrl) {
      location.replace(`${state.serverUrl}/hmi?standby=${encodeURIComponent(location.origin)}`);
    }
  }, [state]);
  return { state, refresh };
}

const secs = (s: number) => (s >= 120 ? `${Math.floor(s / 60)} min` : `${s} s`);

export function DirectBanner({ state, onHandBack }: { state: StandbyState; onHandBack: () => void }) {
  if (state.mode !== "direct") {
    return <div className="hmi-banner hmi-standby-banner" role="status">Standby: waiting for the site server…</div>;
  }
  return (
    <div className="hmi-banner hmi-direct-banner" role="alert">
      <span>{state.serverOk
        ? "Direct mode: this HMI drives the plant. The site server is back: hand control back on the Server screen."
        : "Direct mode: the site server is down and this HMI drives the plant. No logic or schedules run."}</span>
      <button type="button" onClick={onHandBack}>{state.serverOk ? "Server is back" : "Server status"}</button>
    </div>
  );
}

/** The Server screen in direct mode: the server's state and Hand control back (operator PIN). */
export function HandBack({ state, tz, onDone }: { state: StandbyState; tz: string; onDone: () => void }) {
  const { user, can, refresh } = useAuth();
  const unlocked = !!user.username && can("operator");
  const [unlocking, setUnlocking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  if (unlocking && !unlocked) return <PinPad onDone={async () => { setUnlocking(false); await refresh(); }} />;
  const wait = state.serverHealthyS != null ? Math.max(0, state.failbackAfterS - state.serverHealthyS) : null;
  return (
    <section className="hmi-handback" aria-label="Site server">
      <h2>Site server</h2>
      {state.tookOverAt && (
        <p>This HMI took over at {new Date(state.tookOverAt).toLocaleTimeString([], { timeZone: tz, hour: "2-digit", minute: "2-digit" })}.</p>
      )}
      {!state.serverOk && <p className="hmi-handback-bad">The server is not answering{state.serverLostS != null ? ` (${secs(state.serverLostS)})` : ""}.</p>}
      {state.serverOk && wait != null && wait > 0 && (
        <p>The server is back. It has to stay healthy for {secs(Math.ceil(wait))} more before control can be handed back.</p>
      )}
      {state.canFailback && <p className="hmi-handback-ok">The server has been healthy for {secs(state.serverHealthyS ?? 0)}. Control can be handed back.</p>}
      <p className="hmi-small">Handing back returns every hand mode set here to Auto, stops this HMI polling, and sends its log of changes to the server.</p>
      <button type="button" className="hmi-button" disabled={!state.canFailback || busy} onClick={async () => {
        if (!unlocked) { setUnlocking(true); return; }
        setBusy(true);
        try {
          const r = await post<{ released: number; auditSent: number }>("/api/standby/failback", {});
          setDone(`Control handed back${r.released ? `; ${r.released} hand mode${r.released > 1 ? "s" : ""} released` : ""}. Opening the server's HMI…`);
          setError(null);
          onDone();
        } catch (e) { setError(e instanceof SetupError ? e.message : String(e)); } finally { setBusy(false); }
      }}>Hand control back</button>
      {error && <p className="hmi-pin-error" role="alert">{error}</p>}
      {done && <p role="status">{done}</p>}
    </section>
  );
}

/** The web app: a banner while an HMI holds the site. */
export function HeldBanner() {
  const [held, setHeld] = useState<string | null>(null);
  useEffect(() => {
    let stop = false;
    const check = () => fetch("/api/health", { cache: "no-store" }).then((r) => r.json())
      .then((h: { held?: string | null }) => { if (!stop) setHeld(h.held?.startsWith("the HMI") ? h.held : null); })
      .catch(() => undefined);
    void check();
    const timer = window.setInterval(check, 10000);
    return () => { stop = true; window.clearInterval(timer); };
  }, []);
  if (!held) return null;
  return (
    <div className="held-banner" role="alert">
      {held.charAt(0).toUpperCase() + held.slice(1)}. This server is not polling and refuses commands.
    </div>
  );
}
