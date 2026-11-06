// Schedules (main spec 5.4; milestone 7B): occupancy over the week, holiday calendars, one-off
// exceptions, what each schedule applies to, and its copy in a BACnet controller.
import { useEffect, useMemo, useState } from "react";
import { useAuth } from "../auth/AuthGate";
import type { Outcome } from "../setup/ui";
import { ActionButton, Badge, OutcomeBox, outcomeFrom, useLoad } from "../setup/ui";
import { slug } from "../setup/text";
import { getHealth } from "../setup/api";
import { getSite } from "../api";
import type { Calendar, Period, Schedule } from "./schedulesApi";
import {
  acceptController, addSchedule, checkController, DAY_TEXT, DAYS, listCalendars, listSchedules, pushController,
  removeCalendar, removeSchedule, saveCalendar, saveSchedule, stateText,
} from "./schedulesApi";
import "./schedules.css";

const blank = (): Schedule => ({ name: "", dis: "", enabled: true, weekly: {}, calendars: [], exceptions: [], equips: [], tags: [], controller: null });

export function SchedulesPage() {
  const [tab, setTab] = useState<"schedules" | "calendars">("schedules");
  return (
    <div className="schedules-page">
      <div className="tabs pad" role="tablist">
        <button type="button" role="tab" aria-selected={tab === "schedules"} onClick={() => setTab("schedules")}>Schedules</button>
        <button type="button" role="tab" aria-selected={tab === "calendars"} onClick={() => setTab("calendars")}>Holiday calendars</button>
      </div>
      {tab === "schedules" ? <Schedules /> : <Calendars />}
    </div>
  );
}

function Schedules() {
  const { can, user } = useAuth();
  const canEdit = can("operator") && !!user.username;
  const list = useLoad(() => listSchedules(), []);
  const cals = useLoad(() => listCalendars(), []);
  const site = useLoad(() => getSite(), []);
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState<Schedule | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  useEffect(() => {
    if (!selected && !isNew && list.data?.length) setSelected(list.data[0].name);
  }, [list.data, selected, isNew]);
  useEffect(() => {
    const s = list.data?.find((x) => x.name === selected);
    if (s && !isNew) setDraft(structuredClone(s));
  }, [selected, list.data, isNew]);
  useEffect(() => {
    const t = window.setInterval(() => void list.reload(), 30000); // the state and next change move on
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const set = (change: Partial<Schedule>) => draft && setDraft({ ...draft, ...change });
  const setDay = (day: string, periods: Period[]) => draft && set({ weekly: { ...draft.weekly, [day]: periods } });

  const save = async () => {
    if (!draft) return;
    try {
      const body = { ...draft, name: isNew ? slug(draft.dis) : draft.name };
      const saved = isNew ? await addSchedule(body) : await saveSchedule(body);
      setOutcome({ kind: "ok", message: `Saved. Now ${stateText(saved)}.` });
      setIsNew(false);
      setSelected(saved.name);
      await list.reload();
    } catch (e) { setOutcome(outcomeFrom(e)); }
  };

  return (
    <div className="split">
      <section className="list-pane" aria-label="Schedules">
        <div className="pane-head">
          <h2>Schedules</h2>
          {canEdit && <button type="button" className="btn" onClick={() => { setIsNew(true); setSelected(null); setDraft(blank()); setOutcome(null); }}>Add</button>}
        </div>
        <ul className="pick-list">
          {(list.data ?? []).map((s) => (
            <li key={s.name}>
              <button type="button" className={selected === s.name && !isNew ? "active" : ""} onClick={() => { setIsNew(false); setSelected(s.name); setOutcome(null); }}>
                <span className="pick-main">
                  <span>{s.dis}</span>
                  <span className="muted small">{stateText(s)}</span>
                </span>
                <span className="pick-side">
                  {!s.enabled ? <Badge tone="muted">Off</Badge> : s.now?.occupied ? <Badge tone="ok">Occupied</Badge> : <Badge tone="info">Unoccupied</Badge>}
                </span>
              </button>
            </li>
          ))}
          {!list.loading && !(list.data ?? []).length && <li className="muted empty">No schedules yet.</li>}
        </ul>
        {site.data && <p className="muted small">Times are {site.data.ianaTz} time.</p>}
      </section>
      <section className="detail-pane" aria-label="Schedule">
        {draft ? (
          <fieldset className="schedule-editor" disabled={!canEdit}>
            <div className="row">
              <label className="stack grow"><span>Name</span>
                <input aria-label="Schedule name" value={draft.dis} onChange={(e) => set({ dis: e.target.value })} placeholder="Office hours" /></label>
              <label className="check"><input type="checkbox" checked={draft.enabled} onChange={(e) => set({ enabled: e.target.checked })} /> Running</label>
            </div>
            {!isNew && draft.now && <p><strong>{stateText(draft)}</strong> <span className="muted small">({draft.now.why})</span></p>}

            <h3>Occupied every week</h3>
            <table className="grid compact week" aria-label="Weekly pattern">
              <tbody>
                {DAYS.map((day) => {
                  const periods = draft.weekly[day] ?? [];
                  return (
                    <tr key={day}>
                      <th>{DAY_TEXT[day]}</th>
                      <td>
                        {periods.map((p, i) => (
                          <span key={i} className="period">
                            <input type="time" aria-label={`${DAY_TEXT[day]} from`} value={p.start} onChange={(e) => setDay(day, periods.map((q, j) => (j === i ? { ...q, start: e.target.value } : q)))} />
                            to
                            <input type="time" aria-label={`${DAY_TEXT[day]} to`} value={p.end === "24:00" ? "23:59" : p.end} onChange={(e) => setDay(day, periods.map((q, j) => (j === i ? { ...q, end: e.target.value === "23:59" ? "24:00" : e.target.value } : q)))} />
                            <button type="button" className="link danger" aria-label={`Remove ${DAY_TEXT[day]} period`} onClick={() => setDay(day, periods.filter((_, j) => j !== i))}>×</button>
                          </span>
                        ))}
                        {!periods.length && <span className="muted small">Unoccupied all day</span>}
                      </td>
                      <td className="nowrap">
                        <button type="button" className="link" aria-label={`Add a period on ${DAY_TEXT[day]}`} onClick={() => setDay(day, [...periods, { start: "07:00", end: "18:00" }])}>Add</button>{" "}
                        {day !== "mon" && <button type="button" className="link" onClick={() => setDay(day, structuredClone(draft.weekly.mon ?? []))}>Same as Monday</button>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className="muted small">23:59 means until midnight.</p>

            <h3>Holiday calendars</h3>
            <div className="row">
              {(cals.data ?? []).map((c: Calendar) => (
                <label key={c.name} className="check"><input type="checkbox" checked={draft.calendars.includes(c.name)}
                  onChange={(e) => set({ calendars: e.target.checked ? [...draft.calendars, c.name] : draft.calendars.filter((x) => x !== c.name) })} /> {c.dis}</label>
              ))}
              {!(cals.data ?? []).length && <span className="muted small">None yet (Holiday calendars tab). Holidays are Unoccupied all day.</span>}
            </div>

            <h3>One-off exceptions</h3>
            <table className="grid compact" aria-label="Exceptions">
              <tbody>
                {draft.exceptions.map((e, i) => (
                  <tr key={i}>
                    <td><input type="datetime-local" aria-label="Exception from" value={e.start} onChange={(ev) => set({ exceptions: draft.exceptions.map((x, j) => (j === i ? { ...x, start: ev.target.value } : x)) })} /></td>
                    <td><input type="datetime-local" aria-label="Exception to" value={e.end} onChange={(ev) => set({ exceptions: draft.exceptions.map((x, j) => (j === i ? { ...x, end: ev.target.value } : x)) })} /></td>
                    <td>
                      <select aria-label="Exception state" value={e.occupied ? "1" : "0"} onChange={(ev) => set({ exceptions: draft.exceptions.map((x, j) => (j === i ? { ...x, occupied: ev.target.value === "1" } : x)) })}>
                        <option value="1">Occupied</option><option value="0">Unoccupied</option>
                      </select>
                    </td>
                    <td><input aria-label="Exception note" placeholder="Note" value={e.dis ?? ""} onChange={(ev) => set({ exceptions: draft.exceptions.map((x, j) => (j === i ? { ...x, dis: ev.target.value } : x)) })} /></td>
                    <td><button type="button" className="link danger" onClick={() => set({ exceptions: draft.exceptions.filter((_, j) => j !== i) })}>Remove</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <button type="button" className="btn" onClick={() => {
              const now = new Date();
              const pad = (n: number) => String(n).padStart(2, "0");
              const today = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
              set({ exceptions: [...draft.exceptions, { start: `${today}T18:00`, end: `${today}T21:00`, occupied: true, dis: "" }] });
            }}>Add exception</button>
            <p className="muted small">An exception beats a holiday, which beats the week.</p>

            <AppliesTo draft={draft} set={set} />
            <ControllerCopy draft={draft} set={set} isNew={isNew} canEdit={canEdit} onChanged={() => void list.reload()} setOutcome={setOutcome} />

            {canEdit && (
              <div className="actions">
                <ActionButton primary disabled={!draft.dis.trim()} onClick={save}>{isNew ? "Add schedule" : "Save schedule"}</ActionButton>
                {!isNew && <ActionButton danger onClick={async () => {
                  if (!window.confirm(`Remove the schedule ${draft.dis}? Its equipment keeps its current values.`)) return;
                  try { await removeSchedule(draft.name); setSelected(null); setDraft(null); await list.reload(); } catch (e) { setOutcome(outcomeFrom(e)); }
                }}>Remove</ActionButton>}
              </div>
            )}
            <OutcomeBox outcome={outcome} />
          </fieldset>
        ) : <p className="muted">Pick a schedule{canEdit ? ", or add one" : ""}.</p>}
      </section>
    </div>
  );
}

function AppliesTo({ draft, set }: { draft: Schedule; set: (c: Partial<Schedule>) => void }) {
  const site = useLoad(() => getSite(), []);
  const equips = site.data?.equips ?? [];
  const [tag, setTag] = useState("");
  return (
    <>
      <h3>Applies to</h3>
      <div className="equip-picks">
        {equips.map((e) => (
          <label key={e.name} className="check"><input type="checkbox" checked={draft.equips.includes(e.name)}
            onChange={(ev) => set({ equips: ev.target.checked ? [...draft.equips, e.name] : draft.equips.filter((x) => x !== e.name) })} /> {e.dis}</label>
        ))}
      </div>
      <div className="row">
        <span className="small">And all equipment tagged:</span>
        {draft.tags.map((t) => <Badge key={t} tone="info">{t} <button type="button" className="link" aria-label={`Remove tag ${t}`} onClick={() => set({ tags: draft.tags.filter((x) => x !== t) })}>×</button></Badge>)}
        <input aria-label="Tag" placeholder="for example ahu" value={tag} onChange={(e) => setTag(e.target.value)} />
        <button type="button" className="btn" disabled={!tag.trim()} onClick={() => { set({ tags: [...draft.tags, tag.trim()] }); setTag(""); }}>Add tag</button>
      </div>
      {draft.points && (
        <div className="block">
          <p className="small"><strong>{draft.points.length} point{draft.points.length === 1 ? " follows" : "s follow"} this schedule</strong> (occupancy values are set per point in Setup → Mapping table):</p>
          <ul className="small">
            {draft.points.map((p) => <li key={p.name}>{p.dis}: {String(p.occupancy.occupied)} occupied, {String(p.occupancy.unoccupied)} unoccupied</li>)}
          </ul>
        </div>
      )}
    </>
  );
}

function ControllerCopy({ draft, set, isNew, canEdit, onChanged, setOutcome }: {
  draft: Schedule; set: (c: Partial<Schedule>) => void; isNew: boolean; canEdit: boolean; onChanged: () => void; setOutcome: (o: Outcome | null) => void;
}) {
  const health = useLoad(() => getHealth().catch(() => null), []);
  const devices = useMemo(() => (health.data?.devices ?? []).map((d) => d.name), [health.data]);
  const c = draft.controller;
  const st = draft.controllerState;
  return (
    <>
      <h3>Copy in a controller</h3>
      <p className="muted small">Optional: a BACnet/IP controller keeps the times if the server stops. The BMS writes its Schedule object and checks it for local changes.</p>
      <label className="check"><input type="checkbox" checked={!!c} onChange={(e) => set({ controller: e.target.checked ? { device: devices[0] ?? "", instance: 1, valueType: "enumerated", occupied: 1, unoccupied: 0 } : null })} /> Copy this schedule into a controller</label>
      {c && (
        <div className="row">
          <label className="stack"><span>Device</span>
            <select aria-label="Controller device" value={c.device} onChange={(e) => set({ controller: { ...c, device: e.target.value } })}>
              {devices.map((d) => <option key={d}>{d}</option>)}
            </select></label>
          <label className="stack"><span>Schedule object</span><input aria-label="Schedule instance" type="number" value={c.instance} onChange={(e) => set({ controller: { ...c, instance: Number(e.target.value) } })} /></label>
          <label className="stack"><span>Value type</span>
            <select aria-label="Value type" value={c.valueType} onChange={(e) => set({ controller: { ...c, valueType: e.target.value as typeof c.valueType } })}>
              <option value="enumerated">Enumerated (binary)</option><option value="unsigned">Unsigned (multi-state)</option><option value="real">Real</option><option value="boolean">Boolean</option>
            </select></label>
          <label className="stack"><span>Occupied value</span><input aria-label="Controller occupied value" value={String(c.occupied)} onChange={(e) => set({ controller: { ...c, occupied: Number(e.target.value) } })} /></label>
          <label className="stack"><span>Unoccupied value</span><input aria-label="Controller unoccupied value" value={String(c.unoccupied)} onChange={(e) => set({ controller: { ...c, unoccupied: Number(e.target.value) } })} /></label>
        </div>
      )}
      {c && !isNew && (
        <div className="block">
          {st?.inSync === true && <p><Badge tone="ok">In step</Badge> <span className="muted small">checked {st.checkedAt ? new Date(st.checkedAt).toLocaleString() : ""}</span></p>}
          {st?.inSync === false && (
            <div className="note warn">
              <strong>The controller's schedule was changed on the controller.</strong>
              <ul>{(st.differences ?? []).map((d) => <li key={d}>{d}</li>)}</ul>
            </div>
          )}
          {!st && <p className="muted small">Not written to the controller yet. Save first, then write it.</p>}
          <div className="actions">
            {canEdit && <ActionButton onClick={async () => { try { await pushController(draft.name); setOutcome({ kind: "ok", message: "Written to the controller." }); onChanged(); } catch (e) { setOutcome(outcomeFrom(e)); } }}>{st?.inSync === false ? "Write the BMS schedule again" : "Write to the controller"}</ActionButton>}
            <ActionButton onClick={async () => { try { await checkController(draft.name); onChanged(); } catch (e) { setOutcome(outcomeFrom(e)); } }}>Check the controller</ActionButton>
            {canEdit && st?.inSync === false && <ActionButton onClick={async () => { try { await acceptController(draft.name); setOutcome({ kind: "ok", message: "Took the controller's weekly times." }); onChanged(); } catch (e) { setOutcome(outcomeFrom(e)); } }}>Keep the controller's weekly times</ActionButton>}
          </div>
        </div>
      )}
    </>
  );
}

function Calendars() {
  const { can, user } = useAuth();
  const canEdit = can("operator") && !!user.username;
  const list = useLoad(() => listCalendars(), []);
  const [draft, setDraft] = useState<Calendar | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  return (
    <div className="split">
      <section className="list-pane">
        <div className="pane-head">
          <h2>Holiday calendars</h2>
          {canEdit && <button type="button" className="btn" onClick={() => { setIsNew(true); setDraft({ name: "", dis: "", dates: [] }); }}>Add</button>}
        </div>
        <ul className="pick-list">
          {(list.data ?? []).map((c) => (
            <li key={c.name}><button type="button" className={draft?.name === c.name && !isNew ? "active" : ""} onClick={() => { setIsNew(false); setDraft(structuredClone(c)); setOutcome(null); }}>
              <span className="pick-main"><span>{c.dis}</span><span className="muted small">{c.dates.length} dates</span></span>
            </button></li>
          ))}
        </ul>
      </section>
      <section className="detail-pane">
        {draft ? (
          <fieldset className="schedule-editor" disabled={!canEdit}>
            <label className="stack"><span>Name</span><input aria-label="Calendar name" value={draft.dis} onChange={(e) => setDraft({ ...draft, dis: e.target.value })} placeholder="NSW public holidays" /></label>
            <table className="grid compact" aria-label="Holiday dates">
              <thead><tr><th>From</th><th>To (optional)</th><th>Note</th><th /></tr></thead>
              <tbody>
                {draft.dates.map((d, i) => (
                  <tr key={i}>
                    <td><input type="date" aria-label="Holiday from" value={d.start} onChange={(e) => setDraft({ ...draft, dates: draft.dates.map((x, j) => (j === i ? { ...x, start: e.target.value } : x)) })} /></td>
                    <td><input type="date" aria-label="Holiday to" value={d.end ?? ""} onChange={(e) => setDraft({ ...draft, dates: draft.dates.map((x, j) => (j === i ? { ...x, end: e.target.value || null } : x)) })} /></td>
                    <td><input aria-label="Holiday note" value={d.dis ?? ""} onChange={(e) => setDraft({ ...draft, dates: draft.dates.map((x, j) => (j === i ? { ...x, dis: e.target.value } : x)) })} /></td>
                    <td><button type="button" className="link danger" onClick={() => setDraft({ ...draft, dates: draft.dates.filter((_, j) => j !== i) })}>Remove</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <button type="button" className="btn" onClick={() => setDraft({ ...draft, dates: [...draft.dates, { start: new Date().toISOString().slice(0, 10), dis: "" }] })}>Add date</button>
            {canEdit && (
              <div className="actions">
                <ActionButton primary disabled={!draft.dis.trim()} onClick={async () => {
                  try {
                    const body = { ...draft, name: isNew ? slug(draft.dis) : draft.name };
                    const saved = await saveCalendar(body);
                    setDraft(saved); setIsNew(false); setOutcome({ kind: "ok", message: "Saved." }); await list.reload();
                  } catch (e) { setOutcome(outcomeFrom(e)); }
                }}>Save calendar</ActionButton>
                {!isNew && <ActionButton danger onClick={async () => {
                  try { await removeCalendar(draft.name); setDraft(null); await list.reload(); } catch (e) { setOutcome(outcomeFrom(e)); }
                }}>Remove</ActionButton>}
              </div>
            )}
            <OutcomeBox outcome={outcome} />
          </fieldset>
        ) : <p className="muted">Pick a calendar{canEdit ? ", or add one" : ""}.</p>}
      </section>
    </div>
  );
}
