// Command one point (main spec 5.6): set a lasting value, or hand mode for a chosen time, and
// release to auto. Every start/stop and every override is confirmed first. Used by the web
// app and the HMI (bigger controls there).
import { useEffect, useState } from "react";
import type { LiveValue, Point } from "../api";
import { formatValue } from "../format";
import { SetupError } from "../setup/api";
import type { CommandResult } from "./commandsApi";
import { getCommandSettings, hoursText, releaseToAuto, sendCommand } from "./commandsApi";
import "./commands.css";

const show = (v: unknown) => (v === true ? "On" : v === false ? "Off" : String(v));

export function OverrideBadge({ point, tz }: { point: Point; tz?: string }) {
  const o = point.override;
  if (!o) return null;
  const until = new Date(o.endsAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZone: tz });
  return (
    <span className="override-badge" title={`Set by ${o.setBy}`}>
      <span aria-hidden="true">✋</span> Hand: {show(o.value)}{point.unit && typeof o.value === "number" ? ` ${point.unit}` : ""} until {until}
    </span>
  );
}

interface Pending {
  value: unknown;
  mode: "set" | "hand";
  hours?: number;
  text: string;
}

export function CommandPanel({ point, live, onClose, big = false, tz }: {
  point: Point;
  live: LiveValue;
  onClose: () => void;
  big?: boolean;
  tz?: string; // the site's time zone, for "until" times
}) {
  const [choices, setChoices] = useState<{ choicesH: number[]; defaultH: number } | null>(null);
  const [hours, setHours] = useState<number | null>(null);
  const [number, setNumber] = useState<string>(typeof live.value === "number" ? String(live.value) : "");
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    getCommandSettings().then((s) => { setChoices(s); setHours(s.defaultH); }).catch(() => undefined);
  }, []);

  const limits = point.writeLimits;
  const n = Number(number);
  const numberOk = number.trim() !== "" && Number.isFinite(n) && !!limits && n >= limits.min && n <= limits.max;

  const ask = (value: unknown, mode: "set" | "hand") => {
    const h = mode === "hand" ? hours ?? choices?.defaultH ?? 2 : undefined;
    const what = `${point.dis}: ${show(value)}${point.unit && typeof value === "number" ? ` ${point.unit}` : ""}`;
    const text = mode === "hand" ? `Hand mode: ${what} for ${hoursText(h!)}, then back to auto.`
      : point.kind === "Bool" ? `${value ? "Start" : "Stop"}: ${what}.` : `Set ${what}.`;
    // Start/stop and every override are confirmed (main spec 5.6); a setpoint change goes straight on.
    if (mode === "hand" || point.kind === "Bool") setPending({ value, mode, hours: h, text });
    else void run({ value, mode, hours: h, text });
  };

  const run = async (p: Pending) => {
    setBusy(true);
    setMessage(null);
    try {
      const r: CommandResult = await sendCommand(point.id, { value: p.value, mode: p.mode, hours: p.hours, confirm: true });
      const rb = r.readBack;
      setMessage({ ok: true, text: rb && rb.status === "ok" ? `Done. The device now reads ${show(rb.value)}.` : "Sent." });
    } catch (e) {
      setMessage({ ok: false, text: e instanceof SetupError ? e.message : String(e) });
    } finally {
      setBusy(false);
      setPending(null);
    }
  };

  return (
    <div className={`command-panel${big ? " big" : ""}`} role="dialog" aria-label={`Command ${point.dis}`}>
      <div className="command-head">
        <div>
          <h3>{point.dis}</h3>
          <div className="muted small">Now: {formatValue(point, live)}{point.override ? "" : " · auto"}</div>
          {point.override && <OverrideBadge point={point} tz={tz} />}
        </div>
        <button type="button" className="btn-plain" onClick={onClose} aria-label="Close">Close</button>
      </div>

      {pending ? (
        <div className="confirm" role="alertdialog" aria-label="Confirm">
          <p>{pending.text}</p>
          <div className="command-row">
            <button type="button" className="cmd-btn primary" disabled={busy} onClick={() => void run(pending)}>Confirm</button>
            <button type="button" className="cmd-btn" disabled={busy} onClick={() => setPending(null)}>Cancel</button>
          </div>
        </div>
      ) : (
        <>
          {point.kind === "Bool" && (
            <div className="command-row">
              <button type="button" className="cmd-btn" disabled={busy} onClick={() => ask(true, "hand")}>On</button>
              <button type="button" className="cmd-btn" disabled={busy} onClick={() => ask(false, "hand")}>Off</button>
            </div>
          )}
          {point.kind === "Str" && (
            <div className="command-row wrap">
              {(point.enum ?? []).map((e) => (
                <button key={e} type="button" className="cmd-btn" disabled={busy} onClick={() => ask(e, point.type === "sp" ? "set" : "hand")}>{e}</button>
              ))}
            </div>
          )}
          {point.kind === "Number" && (
            limits ? (
              <div className="command-row wrap">
                <button type="button" className="cmd-btn step" aria-label="Down" onClick={() => setNumber(String(Math.max(limits.min, (Number.isFinite(n) ? n : limits.min) - (point.unit === "°C" ? 0.5 : 1))))}>−</button>
                <input className="cmd-input" aria-label="New value" inputMode="decimal" value={number} onChange={(e) => setNumber(e.target.value)} />
                <button type="button" className="cmd-btn step" aria-label="Up" onClick={() => setNumber(String(Math.min(limits.max, (Number.isFinite(n) ? n : limits.min) + (point.unit === "°C" ? 0.5 : 1))))}>+</button>
                <span className="muted small">{limits.min} to {limits.max}{point.unit ? ` ${point.unit}` : ""}</span>
              </div>
            ) : (
              <p className="note warn">No write limits yet: an admin sets them in the point's setup before it can be commanded.</p>
            )
          )}
          {point.kind === "Number" && limits && (
            <div className="command-row">
              <button type="button" className="cmd-btn primary" disabled={busy || !numberOk} onClick={() => ask(n, "set")}>Set</button>
              <button type="button" className="cmd-btn" disabled={busy || !numberOk} onClick={() => ask(n, "hand")}>Hand mode</button>
            </div>
          )}
          {choices && (point.kind !== "Str" || point.type !== "sp") && (
            <label className="command-row hours">
              <span>Hand mode for</span>
              <select aria-label="Hand mode time" value={hours ?? choices.defaultH} onChange={(e) => setHours(Number(e.target.value))}>
                {choices.choicesH.map((h) => <option key={h} value={h}>{hoursText(h)}</option>)}
              </select>
            </label>
          )}
          {point.override && (
            <button type="button" className="cmd-btn" disabled={busy} onClick={async () => {
              setBusy(true);
              try { await releaseToAuto(point.id); setMessage({ ok: true, text: "Released to auto." }); }
              catch (e) { setMessage({ ok: false, text: e instanceof SetupError ? e.message : String(e) }); }
              finally { setBusy(false); }
            }}>Release to auto</button>
          )}
        </>
      )}
      {message && <p className={message.ok ? "ok-text" : "bad-text"} role="status">{message.text}</p>}
    </div>
  );
}
