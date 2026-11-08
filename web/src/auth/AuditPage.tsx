// The audit log (main spec 5.6): filters, before and after, CSV export. Admins see everything;
// it cannot be edited or deleted from the app. Entries are kept for 1 year.
import { useState } from "react";
import { useLoad } from "../setup/ui";
import type { AuditRow } from "./authApi";
import { auditQuery, getAudit } from "./authApi";

const ACTIONS = ["", "login", "support-login", "login-failed", "logout", "first-run", "user-add", "user-change", "user-remove", "password",
  "unlock", "2fa", "settings", "put", "delete", "rename", "import", "commit", "apply-template", "create-from-template",
  "rollout", "reset-override", "unlink", "scan", "update-start", "update", "update-hmi"];

export function AuditTable({ rows }: { rows: AuditRow[] }) {
  return (
    <table className="grid" aria-label="Audit log">
      <thead><tr><th>When</th><th>User</th><th>What</th><th>Details</th></tr></thead>
      <tbody>
        {rows.map((c, i) => (
          <tr key={i} className={c.action === "login-failed" || c.action === "support-login" ? "row-warn" : ""}>
            <td className="nowrap">{new Date(c.ts).toLocaleString()}</td>
            <td>{c.user ?? <span className="muted">–</span>}<div className="mono small muted">{c.client}</div></td>
            <td>{c.summary}</td>
            <td>
              {(c.before != null || c.after != null) && (
                <details>
                  <summary>Before and after</summary>
                  <div className="before-after">
                    <pre>{JSON.stringify(c.before, null, 2)?.slice(0, 4000) ?? "–"}</pre>
                    <pre>{JSON.stringify(c.after, null, 2)?.slice(0, 4000) ?? "–"}</pre>
                  </div>
                </details>
              )}
            </td>
          </tr>
        ))}
        {!rows.length && <tr><td colSpan={4} className="muted">Nothing matches.</td></tr>}
      </tbody>
    </table>
  );
}

export function AuditPage() {
  const [filters, setFilters] = useState<Record<string, string>>({ user: "", action: "", q: "", start: "", end: "", limit: "200" });
  const toIso = (d: string) => (d ? new Date(d).toISOString() : "");
  const query = { ...filters, start: toIso(filters.start), end: toIso(filters.end) };
  const log = useLoad(() => getAudit(query), [JSON.stringify(query)]);
  const set = (k: string, v: string) => setFilters({ ...filters, [k]: v });
  return (
    <div className="main-pane">
      <div className="pane-head">
        <div>
          <h2>Audit log</h2>
          <p className="muted small">Every setup change, scan, login and account change, with who and when. Kept for 1 year.</p>
        </div>
        <a className="btn" href={`/api/audit.csv?${auditQuery({ ...query, limit: "" })}`} download>Export CSV</a>
      </div>
      <div className="row filters" role="group" aria-label="Filters">
        <input aria-label="User" placeholder="User" value={filters.user} onChange={(e) => set("user", e.target.value)} />
        <select aria-label="Type" value={filters.action} onChange={(e) => set("action", e.target.value)}>
          {ACTIONS.map((a) => <option key={a} value={a}>{a || "All types"}</option>)}
        </select>
        <input aria-label="Search" placeholder="Point, device or text" value={filters.q} onChange={(e) => set("q", e.target.value)} />
        <label className="row small">From <input type="datetime-local" value={filters.start} onChange={(e) => set("start", e.target.value)} /></label>
        <label className="row small">To <input type="datetime-local" value={filters.end} onChange={(e) => set("end", e.target.value)} /></label>
        <select aria-label="How many" value={filters.limit} onChange={(e) => set("limit", e.target.value)}>
          <option value="200">Last 200</option><option value="1000">Last 1000</option><option value="5000">Last 5000</option>
        </select>
      </div>
      {log.error && <p className="error">{log.error}</p>}
      <AuditTable rows={log.data ?? []} />
    </div>
  );
}
