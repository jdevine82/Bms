// The change log: every setup change and scan, with who (the client address until logins
// exist, milestone 6) and the before and after.
import { useState } from "react";
import { getChanges } from "./api";
import { useLoad } from "./ui";

export function ChangeLogPage() {
  const [limit, setLimit] = useState(100);
  const log = useLoad(() => getChanges(limit), [limit]);
  return (
    <div className="main-pane">
      <div className="pane-head">
        <h2>Change log</h2>
        <div className="row">
          <select aria-label="How many" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
            <option value={100}>Last 100</option>
            <option value={500}>Last 500</option>
            <option value={1000}>Last 1000</option>
          </select>
          <button type="button" className="btn" onClick={() => void log.reload()}>Refresh</button>
        </div>
      </div>
      {log.error && <p className="error">{log.error}</p>}
      <table className="grid" aria-label="Changes">
        <thead><tr><th>When</th><th>Who</th><th>What</th><th>Details</th></tr></thead>
        <tbody>
          {(log.data ?? []).map((c, i) => (
            <tr key={i}>
              <td className="nowrap">{new Date(c.ts).toLocaleString()}</td>
              <td className="mono small">{c.client}</td>
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
        </tbody>
      </table>
    </div>
  );
}
