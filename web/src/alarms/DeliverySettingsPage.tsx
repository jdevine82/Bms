// Setup → Alarm delivery (main spec 5.5.1): channels per level, SMTP, ClickSend and ntfy, the
// recipient list with quiet hours, and test messages. Secrets are never typed here: the page
// names the environment variables on the server that hold them.
import { useEffect, useState } from "react";
import type { Outcome } from "../setup/ui";
import { ActionButton, OutcomeBox, outcomeFrom } from "../setup/ui";
import type { AlarmSettings, Level, Recipient } from "./alarmsApi";
import { getAlarmSettings, LEVEL_SHAPE, LEVEL_TEXT, putAlarmSettings, sendTest } from "./alarmsApi";

const LEVELS: Level[] = ["info", "warning", "critical"];
const CH = ["email", "ntfy", "sms"] as const;
const CH_TEXT = { email: "Email", ntfy: "ntfy push", sms: "SMS" };

export function DeliverySettingsPage() {
  const [s, setS] = useState<AlarmSettings | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [tests, setTests] = useState<Record<string, string>>({});
  useEffect(() => {
    getAlarmSettings().then((r) => { setS(r.settings); setWarnings(r.warnings); }).catch((e) => setOutcome(outcomeFrom(e)));
  }, []);
  if (!s) return <OutcomeBox outcome={outcome ?? { kind: "info", message: "Loading…" }} />;
  const setR = (i: number, change: Partial<Recipient>) => setS({ ...s, recipients: s.recipients.map((r, j) => (j === i ? { ...r, ...change } : r)) });
  const text = (label: string, value: string | number | null, onChange: (v: string) => void, opts: { type?: string; placeholder?: string } = {}) => (
    <label className="stack"><span>{label}</span>
      <input aria-label={label} type={opts.type ?? "text"} placeholder={opts.placeholder} value={value ?? ""} onChange={(e) => onChange(e.target.value)} /></label>
  );

  return (
    <div className="main-pane delivery-settings">
      <h2>Alarm delivery</h2>
      <p className="muted small">Who is told about alarms, and how. Recipients' details are personal data kept on this server only. Messages carry the alarm, its value and the site name, nothing more.</p>
      {warnings.length > 0 && <div className="note warn"><ul>{warnings.map((w) => <li key={w}>{w}</li>)}</ul></div>}

      <h3>Channels per level</h3>
      <table className="grid compact" aria-label="Channels per level">
        <thead><tr><th>Level</th><th>Screen</th>{CH.map((c) => <th key={c}>{CH_TEXT[c]}</th>)}</tr></thead>
        <tbody>
          {LEVELS.map((l) => (
            <tr key={l}>
              <td><span className={`level ${l}`}>{LEVEL_SHAPE[l]} {LEVEL_TEXT[l]}</span></td>
              <td>Always</td>
              {CH.map((c) => (
                <td key={c}>
                  <input type="checkbox" aria-label={`${LEVEL_TEXT[l]} by ${CH_TEXT[c]}`} checked={s.channels[l].includes(c)}
                    onChange={(e) => setS({ ...s, channels: { ...s.channels, [l]: e.target.checked ? [...s.channels[l], c] : s.channels[l].filter((x) => x !== c) } })} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>

      <div className="settings-grid">
        <fieldset>
          <legend>Email (SMTP)</legend>
          {text("SMTP server", s.email.host, (v) => setS({ ...s, email: { ...s.email, host: v } }), { placeholder: "smtp.office365.com" })}
          {text("Port", s.email.port, (v) => setS({ ...s, email: { ...s.email, port: Number(v) } }), { type: "number" })}
          <label className="stack"><span>Security</span>
            <select aria-label="Security" value={s.email.security} onChange={(e) => setS({ ...s, email: { ...s.email, security: e.target.value as AlarmSettings["email"]["security"] } })}>
              <option value="starttls">STARTTLS (port 587)</option><option value="ssl">SSL/TLS (port 465)</option><option value="none">None (local relay only)</option>
            </select></label>
          {text("Sender address", s.email.from, (v) => setS({ ...s, email: { ...s.email, from: v } }), { placeholder: "bms@yourcompany.com.au" })}
          {text("User name", s.email.username, (v) => setS({ ...s, email: { ...s.email, username: v } }))}
          {text("Password variable on the server", s.email.passwordEnv, (v) => setS({ ...s, email: { ...s.email, passwordEnv: v } }), { placeholder: "BMS_SMTP_PASSWORD" })}
        </fieldset>
        <fieldset>
          <legend>SMS (ClickSend)</legend>
          {text("ClickSend user name", s.sms.username, (v) => setS({ ...s, sms: { ...s.sms, username: v } }))}
          {text("API key variable on the server", s.sms.apiKeyEnv, (v) => setS({ ...s, sms: { ...s.sms, apiKeyEnv: v } }), { placeholder: "BMS_CLICKSEND_API_KEY" })}
          {text("Sender name or number (optional)", s.sms.from, (v) => setS({ ...s, sms: { ...s.sms, from: v } }))}
        </fieldset>
        <fieldset>
          <legend>ntfy push</legend>
          {text("ntfy server", s.ntfy.server, (v) => setS({ ...s, ntfy: { ...s.ntfy, server: v } }))}
          {text("Access token variable (self-hosted, optional)", s.ntfy.tokenEnv, (v) => setS({ ...s, ntfy: { ...s.ntfy, tokenEnv: v || null } }))}
          <p className="muted small">On ntfy.sh anyone who knows a topic can read it: use long, random topics.</p>
        </fieldset>
      </div>

      <h3>Recipients</h3>
      <div className="table-scroll">
        <table className="grid editable" aria-label="Recipients">
          <thead><tr><th>Name</th><th>Email</th><th>Mobile</th><th>ntfy topic</th>{LEVELS.map((l) => <th key={l}>{LEVEL_TEXT[l]}</th>)}<th>Quiet hours (Warning)</th><th>On</th><th>Test</th><th /></tr></thead>
          <tbody>
            {s.recipients.map((r, i) => (
              <tr key={r.id}>
                <td><input aria-label="Recipient name" value={r.name} onChange={(e) => setR(i, { name: e.target.value })} /></td>
                <td><input aria-label={`Email of ${r.name}`} value={r.email ?? ""} onChange={(e) => setR(i, { email: e.target.value })} /></td>
                <td><input aria-label={`Mobile of ${r.name}`} value={r.phone ?? ""} placeholder="+614…" onChange={(e) => setR(i, { phone: e.target.value })} /></td>
                <td><input aria-label={`ntfy topic of ${r.name}`} value={r.ntfyTopic ?? ""} onChange={(e) => setR(i, { ntfyTopic: e.target.value })} /></td>
                {LEVELS.map((l) => (
                  <td key={l}><input type="checkbox" aria-label={`${r.name} receives ${LEVEL_TEXT[l]}`} checked={!!r.levels?.[l]}
                    onChange={(e) => setR(i, { levels: { ...r.levels, [l]: e.target.checked } })} /></td>
                ))}
                <td className="nowrap">
                  <input type="checkbox" aria-label={`Quiet hours for ${r.name}`} checked={!!r.quietHours}
                    onChange={(e) => setR(i, { quietHours: e.target.checked ? { start: "22:00", end: "06:00" } : null })} />
                  {r.quietHours && (
                    <>
                      <input className="narrow" type="time" aria-label="Quiet from" value={r.quietHours.start} onChange={(e) => setR(i, { quietHours: { ...r.quietHours!, start: e.target.value } })} />
                      <input className="narrow" type="time" aria-label="Quiet to" value={r.quietHours.end} onChange={(e) => setR(i, { quietHours: { ...r.quietHours!, end: e.target.value } })} />
                    </>
                  )}
                </td>
                <td><input type="checkbox" aria-label={`${r.name} enabled`} checked={r.enabled !== false} onChange={(e) => setR(i, { enabled: e.target.checked })} /></td>
                <td className="nowrap">
                  {CH.map((c) => (
                    <button key={c} type="button" className="link" title="Save first; tests use the saved settings" onClick={async () => {
                      setTests((t) => ({ ...t, [`${r.id}-${c}`]: "sending…" }));
                      try {
                        const res = await sendTest(r.id, c);
                        setTests((t) => ({ ...t, [`${r.id}-${c}`]: res.ok ? "delivered" : `failed: ${res.error}` }));
                      } catch (e) { setTests((t) => ({ ...t, [`${r.id}-${c}`]: `failed: ${outcomeFrom(e).message}` })); }
                    }}>{CH_TEXT[c]}</button>
                  ))}
                  {CH.map((c) => tests[`${r.id}-${c}`] && <div key={c} className="small">{CH_TEXT[c]}: {tests[`${r.id}-${c}`]}</div>)}
                </td>
                <td><button type="button" className="link danger" onClick={() => setS({ ...s, recipients: s.recipients.filter((_, j) => j !== i) })}>Remove</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button type="button" className="btn" onClick={() => setS({ ...s, recipients: [...s.recipients, {
        id: Math.random().toString(36).slice(2, 10), name: "New recipient", levels: { info: false, warning: true, critical: true }, quietHours: null, enabled: true,
      }] })}>Add recipient</button>
      <p className="muted small">Critical alarms are never held by quiet hours. Held warnings go out as one summary when the quiet hours end.</p>

      <div className="actions">
        <ActionButton primary onClick={async () => {
          try {
            const r = await putAlarmSettings(s);
            setS(r.settings); setWarnings(r.warnings);
            setOutcome({ kind: "ok", message: "Saved." });
          } catch (e) { setOutcome(outcomeFrom(e)); }
        }}>Save alarm delivery settings</ActionButton>
      </div>
      <OutcomeBox outcome={outcome} />
    </div>
  );
}
