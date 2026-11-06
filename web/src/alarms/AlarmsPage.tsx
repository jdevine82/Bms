// Alarms (main spec 5.5): active list with acknowledge and comments, history, and the delivery log.
import { useEffect, useMemo, useState } from "react";
import { useAuth } from "../auth/AuthGate";
import type { Outcome } from "../setup/ui";
import { ActionButton, Badge, OutcomeBox, outcomeFrom, useLoad } from "../setup/ui";
import type { Alarm, AlarmEvent, Delivery, Level } from "./alarmsApi";
import { ackAlarms, commentAlarm, getAlarm, LEVEL_SHAPE, LEVEL_TEXT, listAlarms, listDeliveries } from "./alarmsApi";
import { useAlarmChanges, useOpenAlarms } from "./useAlarms";
import "./alarms.css";

export function LevelTag({ level }: { level: Level }) {
  return (
    <span className={`level ${level}`}>
      <span aria-hidden="true">{LEVEL_SHAPE[level]}</span> {LEVEL_TEXT[level]}
    </span>
  );
}

function valueText(a: Alarm): string {
  const d = a.detail ?? {};
  if (d.value === undefined || d.value === null) return "";
  const v = typeof d.value === "number" ? Math.round(d.value * 100) / 100 : d.value === true ? "on" : d.value === false ? "off" : d.value;
  return `${v}${d.unit ? ` ${d.unit}` : ""}${d.limit !== undefined ? ` (limit ${d.limit})` : ""}`;
}

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "");

function StateTag({ a }: { a: Alarm }) {
  if (a.closed) return <Badge tone="muted">Closed</Badge>;
  if (a.active && !a.ackedAt) return <Badge tone="bad">Active</Badge>;
  if (a.active) return <Badge tone="warn">Active, acknowledged</Badge>;
  return <Badge tone="info">Back to normal, not acknowledged</Badge>;
}

export function AlarmsPage() {
  const [tab, setTab] = useState<"active" | "history" | "deliveries">("active");
  return (
    <div className="alarms-page">
      <div className="tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === "active"} onClick={() => setTab("active")}>Active</button>
        <button type="button" role="tab" aria-selected={tab === "history"} onClick={() => setTab("history")}>History</button>
        <button type="button" role="tab" aria-selected={tab === "deliveries"} onClick={() => setTab("deliveries")}>Notifications sent</button>
      </div>
      {tab === "active" && <ActiveAlarms />}
      {tab === "history" && <History />}
      {tab === "deliveries" && <Deliveries />}
    </div>
  );
}

function ActiveAlarms() {
  const { can } = useAuth();
  const { alarms, refresh } = useOpenAlarms();
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [comment, setComment] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const canAck = can("operator");
  const unacked = alarms.filter((a) => !a.ackedAt);

  return (
    <div className="split wide-right">
      <section className="main-pane">
        <h2>Active alarms</h2>
        <p className="muted small">An alarm leaves this list once it is acknowledged and back to normal.</p>
        <table className="grid" aria-label="Active alarms">
          <thead>
            <tr>
              {canAck && (
                <th>
                  <input type="checkbox" aria-label="Select all unacknowledged" checked={unacked.length > 0 && unacked.every((a) => picked.has(a.id))}
                    onChange={(e) => setPicked(e.target.checked ? new Set(unacked.map((a) => a.id)) : new Set())} />
                </th>
              )}
              <th>Level</th><th>Alarm</th><th>Value</th><th>Raised</th><th>State</th>
            </tr>
          </thead>
          <tbody>
            {alarms.map((a) => (
              <tr key={a.id} className={`clickable ${open === a.id ? "selected" : ""} ${a.active && !a.ackedAt ? `unacked ${a.level}` : ""}`}
                onClick={() => setOpen(a.id)} data-alarm={a.message}>
                {canAck && (
                  <td onClick={(e) => e.stopPropagation()}>
                    {!a.ackedAt && (
                      <input type="checkbox" aria-label={`Select ${a.message}`} checked={picked.has(a.id)}
                        onChange={() => { const n = new Set(picked); if (n.has(a.id)) n.delete(a.id); else n.add(a.id); setPicked(n); }} />
                    )}
                  </td>
                )}
                <td><LevelTag level={a.level} /></td>
                <td>{a.message}<div className="muted small">{a.sourceDis}</div></td>
                <td>{valueText(a)}</td>
                <td className="nowrap">{when(a.raisedAt)}</td>
                <td><StateTag a={a} />{a.ackedBy && <div className="muted small">by {a.ackedBy}</div>}</td>
              </tr>
            ))}
            {!alarms.length && <tr><td colSpan={canAck ? 6 : 5} className="muted">No active alarms.</td></tr>}
          </tbody>
        </table>
        {canAck && (
          <div className="bulk">
            <span className="muted small">{picked.size} selected</span>
            <input aria-label="Comment" placeholder="Comment (optional)" value={comment} onChange={(e) => setComment(e.target.value)} />
            <ActionButton primary disabled={!picked.size} onClick={async () => {
              try {
                const r = await ackAlarms([...picked], comment);
                setOutcome({ kind: "ok", message: `Acknowledged ${r.acknowledged.length}.` });
                setPicked(new Set()); setComment(""); refresh();
              } catch (e) { setOutcome(outcomeFrom(e)); }
            }}>Acknowledge</ActionButton>
          </div>
        )}
        <OutcomeBox outcome={outcome} />
      </section>
      <aside className="side-pane" aria-label="Alarm details">
        {open ? <AlarmDetail id={open} onChanged={refresh} /> : <p className="muted">Pick an alarm to see its history.</p>}
      </aside>
    </div>
  );
}

function AlarmDetail({ id, onChanged }: { id: string; onChanged: () => void }) {
  const { can } = useAuth();
  const detail = useLoad(() => getAlarm(id), [id]);
  const [text, setText] = useState("");
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  useAlarmChanges(() => void detail.reload());
  const a = detail.data;
  if (!a) return <p className="muted">Loading…</p>;
  return (
    <>
      <p className="eyebrow">Alarm</p>
      <h2>{a.message}</h2>
      <p><LevelTag level={a.level} /> <StateTag a={a} /></p>
      <p className="muted small">{a.sourceDis} <span className="mono">{a.source}</span></p>
      <h3>History</h3>
      <ul className="events">
        {a.events.map((e: AlarmEvent, i) => (
          <li key={i}>
            <span className="nowrap small muted">{when(e.ts)}</span>{" "}
            <strong>{{ raised: "Raised", returned: "Back to normal", acked: "Acknowledged", comment: "Comment" }[e.kind]}</strong>
            {e.user && <> by {e.user}</>}
            {e.text && <div className="small">{e.text}</div>}
          </li>
        ))}
      </ul>
      {can("operator") && (
        <div className="row">
          <input aria-label="Add a comment" placeholder="Add a comment" value={text} onChange={(e) => setText(e.target.value)} />
          <ActionButton disabled={!text.trim()} onClick={async () => {
            try { await commentAlarm(a.id, text); setText(""); await detail.reload(); onChanged(); }
            catch (e) { setOutcome(outcomeFrom(e)); }
          }}>Add</ActionButton>
        </div>
      )}
      <OutcomeBox outcome={outcome} />
      <h3>Notifications</h3>
      {a.deliveries.length ? <DeliveryTable rows={a.deliveries} compact /> : <p className="muted small">None sent for this alarm.</p>}
    </>
  );
}

function History() {
  const [level, setLevel] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const filters = useMemo(() => ({ ...(level ? { level } : {}), ...(q ? { q } : {}), limit: "500" }), [level, q]);
  const list = useLoad(() => listAlarms("all", filters), [JSON.stringify(filters)]);
  useAlarmChanges(() => void list.reload());
  return (
    <div className="split wide-right">
      <section className="main-pane">
        <h2>Alarm history</h2>
        <div className="row filters">
          <select aria-label="Level" value={level} onChange={(e) => setLevel(e.target.value)}>
            <option value="">All levels</option><option value="critical">Critical</option><option value="warning">Warning</option><option value="info">Info</option>
          </select>
          <input aria-label="Search alarms" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <table className="grid" aria-label="Alarm history">
          <thead><tr><th>Level</th><th>Alarm</th><th>Raised</th><th>Back to normal</th><th>Acknowledged</th></tr></thead>
          <tbody>
            {(list.data ?? []).map((a) => (
              <tr key={a.id} className={`clickable ${open === a.id ? "selected" : ""}`} onClick={() => setOpen(a.id)}>
                <td><LevelTag level={a.level} /></td>
                <td>{a.message}<div className="muted small">{a.sourceDis}</div></td>
                <td className="nowrap">{when(a.raisedAt)}</td>
                <td className="nowrap">{a.active ? <Badge tone="bad">Still active</Badge> : when(a.returnedAt)}</td>
                <td className="nowrap">{a.ackedAt ? `${when(a.ackedAt)} by ${a.ackedBy}` : <span className="muted">Not yet</span>}</td>
              </tr>
            ))}
            {!list.loading && !(list.data ?? []).length && <tr><td colSpan={5} className="muted">No alarms.</td></tr>}
          </tbody>
        </table>
      </section>
      <aside className="side-pane">{open ? <AlarmDetail id={open} onChanged={() => void list.reload()} /> : <p className="muted">Pick an alarm.</p>}</aside>
    </div>
  );
}

function DeliveryTable({ rows, compact }: { rows: Delivery[]; compact?: boolean }) {
  const tone = (s: Delivery["status"]) => (s === "sent" ? "ok" : s === "failed" ? "bad" : s === "held" ? "info" : "warn");
  return (
    <table className={`grid ${compact ? "compact" : ""}`} aria-label="Notifications">
      <thead><tr><th>When</th><th>To</th><th>By</th><th>Result</th></tr></thead>
      <tbody>
        {rows.map((d) => (
          <tr key={d.id}>
            <td className="nowrap small">{when(d.createdAt)}{d.test && <div className="muted">test</div>}</td>
            <td>{d.recipient}<div className="muted small mono">{d.address}</div></td>
            <td>{{ email: "Email", sms: "SMS", ntfy: "ntfy push" }[d.channel]}</td>
            <td>
              <Badge tone={tone(d.status)}>{{ sent: "Sent", failed: "Failed", held: "Held (quiet hours)", pending: d.attempts ? "Retrying" : "Sending" }[d.status]}</Badge>
              {d.status === "held" && d.holdUntil && <div className="small muted">until {new Date(d.holdUntil).toLocaleTimeString()}</div>}
              {d.error && <div className="small bad-text">{d.error}</div>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Deliveries() {
  const list = useLoad(() => listDeliveries(500), []);
  useEffect(() => {
    const t = window.setInterval(() => void list.reload(), 15000);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <div className="main-pane">
      <h2>Notifications sent</h2>
      <p className="muted small">Every email, SMS and ntfy push: sent, held for quiet hours, retrying or failed.</p>
      <DeliveryTable rows={list.data ?? []} />
    </div>
  );
}

/** The header button: unacknowledged alarms by level. */
export function AlarmButton({ active, onClick }: { active: boolean; onClick: () => void }) {
  const { summary } = useOpenAlarms();
  const u = summary?.unacked ?? { critical: 0, warning: 0, info: 0 };
  const total = u.critical + u.warning + u.info;
  const worst: Level | null = u.critical ? "critical" : u.warning ? "warning" : u.info ? "info" : null;
  return (
    <button type="button" className={`${active ? "active" : ""} alarm-button ${worst ?? ""}`} onClick={onClick}
      aria-label={`Alarms: ${total} not acknowledged`}>
      Alarms{total > 0 && <span className="count">{worst && LEVEL_SHAPE[worst]} {total}</span>}
    </button>
  );
}
