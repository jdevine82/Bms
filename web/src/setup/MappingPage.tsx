// Room mapping table: every point with its equipment, room and tags, editable in place,
// and as a CSV file (main spec 5.1.1; mockup sheet 4). Saved as one checked change.
import { useEffect, useMemo, useState } from "react";
import { getPoints } from "../api";
import type { Issue, SiteConfig } from "./api";
import { commit, download, getConfig } from "./api";
import { verdict } from "./checks";
import { applyCsv, pointsToCsv } from "./csv";
import { PointGrid } from "./PointGrid";
import type { Outcome } from "./ui";
import { ActionButton, FilePick, OutcomeBox, outcomeFrom } from "./ui";

function changedCount(a: SiteConfig, b: SiteConfig): number {
  const before = new Map(a.points.map((p) => [p.name, JSON.stringify(p)]));
  const spaces = b.spaces.length - a.spaces.length;
  return b.points.filter((p) => before.get(p.name) !== JSON.stringify(p)).length + Math.max(0, spaces);
}

export function MappingPage() {
  const [base, setBase] = useState<{ config: SiteConfig; revision: string } | null>(null);
  const [work, setWork] = useState<SiteConfig | null>(null);
  const [statuses, setStatuses] = useState<Record<string, string>>({});
  const [server, setServer] = useState<Issue[]>([]);
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  const load = async () => {
    try {
      const [loaded, points] = await Promise.all([getConfig(), getPoints()]);
      setBase(loaded);
      setWork(loaded.config);
      setStatuses(Object.fromEntries(points.map((p) => [p.name, p.cur.status])));
      setServer([]);
    } catch (e) {
      setOutcome(outcomeFrom(e));
    }
  };
  useEffect(() => {
    void load();
  }, []);

  const changes = useMemo(() => (base && work ? changedCount(base.config, work) : 0), [base, work]);
  const result = useMemo(() => (work ? verdict(work, statuses, server) : { errors: [], warnings: [] }), [work, statuses, server]);

  if (!work || !base) return <OutcomeBox outcome={outcome ?? { kind: "info", message: "Loading…" }} />;

  const save = async (check: boolean) => {
    try {
      const r = await commit(work, base.revision, `mapping table: changed ${changes} point${changes === 1 ? "" : "s"}`, check);
      setServer(r.warnings);
      if (check) {
        setOutcome({ kind: "ok", message: "The checks pass. Nothing was saved yet." });
      } else {
        setOutcome({ kind: "ok", message: `Saved ${changes} change${changes === 1 ? "" : "s"}; they are live.` });
        await load();
      }
    } catch (e) {
      const o = outcomeFrom(e);
      setServer(o.issues ?? []);
      setOutcome(o);
    }
  };

  return (
    <div className="split wide-right">
      <section className="main-pane">
        <div className="pane-head">
          <div>
            <h2>Room mapping table</h2>
            <p className="muted small">
              Edit any cell in place. Select rows to set a room, equipment or tags on many points at once.
            </p>
          </div>
          <div className="row">
            <FilePick
              label="Import CSV"
              accept=".csv,text/csv"
              onText={(text, name) => {
                const r = applyCsv(work, text);
                setWork(r.config);
                setOutcome({
                  kind: r.problems.length ? "error" : "info",
                  message:
                    `${name}: ${r.updated} point${r.updated === 1 ? "" : "s"} changed` +
                    (r.roomsAdded.length ? `, rooms added: ${r.roomsAdded.join(", ")}` : "") +
                    (r.unknown.length ? `; ${r.unknown.length} names not found: ${r.unknown.slice(0, 5).join(", ")}` : "") +
                    ". Nothing is saved until you save.",
                  issues: r.problems.map((m) => ({ severity: "error" as const, where: name, message: m })),
                });
              }}
            />
            <button type="button" className="btn" onClick={() => download(`${work.site.name}-points.csv`, pointsToCsv(work), "text/csv")}>
              Export CSV
            </button>
          </div>
        </div>
        <PointGrid config={work} onChange={setWork} statuses={statuses} canRename={() => false} />
      </section>
      <aside className="side-pane" aria-label="Verify">
        <p className="eyebrow">Verify and save</p>
        <h2>
          {result.errors.length
            ? `${result.errors.length} error${result.errors.length > 1 ? "s" : ""} block saving`
            : changes
              ? `${changes} unsaved change${changes > 1 ? "s" : ""}`
              : "No changes"}
        </h2>
        <dl className="kv">
          <div>
            <dt>Points</dt>
            <dd>{work.points.length}</dd>
          </div>
          <div>
            <dt>Communicating</dt>
            <dd>{Object.values(statuses).filter((s) => s === "ok" || s === "stale").length}</dd>
          </div>
          <div>
            <dt>Equipment from templates</dt>
            <dd>
              {work.equips.filter((e) => e.template).length} of {work.equips.length}
            </dd>
          </div>
          <div>
            <dt>Rooms</dt>
            <dd>{work.spaces.length}</dd>
          </div>
        </dl>
        {result.errors.length > 0 && (
          <div className="note bad">
            <strong>Errors</strong>
            <ul>{result.errors.map((e) => <li key={e}>{e}</li>)}</ul>
          </div>
        )}
        {result.warnings.length > 0 && (
          <div className="note warn">
            <strong>Warnings (can be accepted)</strong>
            <ul>{result.warnings.map((e) => <li key={e}>{e}</li>)}</ul>
          </div>
        )}
        <OutcomeBox outcome={outcome} />
        <div className="actions column">
          <ActionButton disabled={!changes} onClick={() => save(true)}>Check</ActionButton>
          <ActionButton primary disabled={!changes || result.errors.length > 0} onClick={() => save(false)}>
            Save changes
          </ActionButton>
          <button type="button" className="btn" disabled={!changes} onClick={() => { setWork(base.config); setServer([]); setOutcome(null); }}>
            Discard changes
          </button>
        </div>
        <p className="muted small">Saved as one change in the change log. Graphic placement arrives with the graphics editor.</p>
      </aside>
    </div>
  );
}
