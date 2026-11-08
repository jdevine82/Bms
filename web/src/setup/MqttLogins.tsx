// Device logins on the site's own MQTT broker (Mosquitto in bms-app): Setup -> Buses and networks,
// on the broker at 127.0.0.1. The BMS makes each password, shows it once, and does not keep it.
import { useState } from "react";
import { del, get, post } from "./api";
import type { Outcome } from "./ui";
import { ActionButton, OutcomeBox, outcomeFrom, useLoad } from "./ui";

interface Logins { available: boolean; logins: string[] }

export function MqttLogins() {
  const data = useLoad(() => get<Logins>("/api/mqtt-logins"), []);
  const [name, setName] = useState("");
  const [shown, setShown] = useState<{ name: string; password: string } | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  if (!data.data?.available) return null;

  const give = (r: { name: string; password: string }) => { setShown(r); setOutcome(null); void data.reload(); };
  return (
    <section className="mqtt-logins" aria-label="Device logins">
      <h3>Device logins</h3>
      <p className="muted small">Each MQTT device (a Tasmota or Shelly plug, Zigbee2MQTT…) logs in to this broker with its own name and
        password. The BMS makes the password and shows it once: type it into the device's MQTT settings straight away.</p>
      {shown && (
        <div className="note" role="status">
          Password for <strong>{shown.name}</strong> (shown once): <code className="mono" data-testid="mqtt-password">{shown.password}</code>{" "}
          <button type="button" className="btn" onClick={() => void navigator.clipboard?.writeText(shown.password)}>Copy</button>{" "}
          <button type="button" className="btn" onClick={() => setShown(null)}>Done</button>
        </div>
      )}
      <ul className="pick-list compact">
        {(data.data.logins).map((n) => (
          <li key={n} className="row">
            <span className="mono">{n}</span>
            <ActionButton onClick={async () => {
              if (!window.confirm(`Give ${n} a new password? The device stops connecting until you enter it there.`)) return;
              try { give(await post<{ name: string; password: string }>(`/api/mqtt-logins/${encodeURIComponent(n)}/reset`, {})); }
              catch (e) { setOutcome(outcomeFrom(e)); }
            }}>New password</ActionButton>
            <ActionButton danger onClick={async () => {
              if (!window.confirm(`Remove the login ${n}? That device can no longer connect.`)) return;
              try { await del(`/api/mqtt-logins/${encodeURIComponent(n)}`); setShown(null); await data.reload(); }
              catch (e) { setOutcome(outcomeFrom(e)); }
            }}>Remove</ActionButton>
          </li>
        ))}
        {!data.data.logins.length && <li className="muted small">No device logins yet.</li>}
      </ul>
      <div className="row">
        <input aria-label="New login name" placeholder="for example plug-kitchen" value={name} onChange={(e) => setName(e.target.value)} />
        <ActionButton disabled={!name.trim()} onClick={async () => {
          try { give(await post<{ name: string; password: string }>("/api/mqtt-logins", { name: name.trim() })); setName(""); }
          catch (e) { setOutcome(outcomeFrom(e)); }
        }}>Add login</ActionButton>
      </div>
      <OutcomeBox outcome={outcome} />
    </section>
  );
}
