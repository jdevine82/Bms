// Setup > Updates (milestone 12b, decision 031): upload a release; on a site server the updater on
// the Proxmox host snapshots, installs, checks the site and rolls back if the check fails, then the
// release goes to the standby HMIs. On a Pi it restarts twice and switches back if the check fails.
import { useCallback, useEffect, useRef, useState } from "react";
import { AuditTable } from "../auth/AuditPage";
import { getAudit, type AuditRow } from "../auth/authApi";
import { get } from "./api";
import type { Outcome } from "./ui";
import { ActionButton, OutcomeBox } from "./ui";

interface Step { at: string; text: string }
interface AgentStatus {
  state: "idle" | "running" | "passed" | "rolled-back" | "failed" | "unreachable";
  version?: string; from?: string | null; reason?: string | null; started?: string; finished?: string | null;
  steps?: Step[];
}
interface PiUpdate { state: string; version: string; from: string | null; reason: string | null; at: string }
interface Hmi {
  url: string; name: string | null; mode: string | null; version: string | null; lastUpdate: PiUpdate | null;
  push: { version: string; result: string; at: string } | null;
}
interface UpdateStatus {
  kind: "server" | "pi" | "none"; version: string; mode: string;
  agent?: AgentStatus; lastUpdate?: PiUpdate | null; hmis?: Hmi[];
}

const RESULT: Record<string, string> = {
  passed: "Passed: the new version is running.",
  "rolled-back": "Failed, and the site was put back as it was before the update.",
  failed: "Failed.",
};
const when = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString() : "");

export function UpdatesPage() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [offline, setOffline] = useState(false);
  const [history, setHistory] = useState<AuditRow[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const s = await get<UpdateStatus>("/api/updates");
      setStatus(s);
      // The "Updating from … to …" note goes once the result is shown below.
      if (s.agent && s.agent.state !== "running") setOutcome((o) => (o?.kind === "ok" && o.message.startsWith("Updating") ? null : o));
      setOffline(false);
      setHistory(await getAudit({ kind: "update", limit: "30" }));
    } catch {
      setOffline(true); // the backend restarts during an update
    }
  }, []);
  const running = status?.agent?.state === "running" || offline;
  useEffect(() => {
    void load();
    const timer = window.setInterval(load, running ? 2000 : 15000);
    return () => window.clearInterval(timer);
  }, [load, running]);

  if (!status) return <section className="main-pane"><h2>Updates</h2><p className="muted">Loading…</p></section>;
  const agent = status.agent;
  return (
    <section className="main-pane" aria-labelledby="up-h">
      <h2 id="up-h">Updates</h2>
      <p>This {status.kind === "pi" ? "Pi" : "site"} runs version <strong data-testid="up-version">{status.version}</strong>.</p>
      {status.kind === "server" && (
        <p className="muted">An update goes through the updater on the Proxmox host. It snapshots both containers, installs the release,
          and checks that the new version answers, that the same devices are online, and that it stays up for a minute.
          If the check fails, the site is rolled back to the snapshot. A passed update is then sent to the standby HMIs.</p>
      )}
      {status.kind === "pi" && (
        <p className="muted">The Pi restarts twice and is back in about 3 minutes. If the new version does not answer, the Pi
          switches back to {status.version}. {status.mode === "standby" ? "Meanwhile it cannot take over from the server." : ""}</p>
      )}
      {status.kind === "none" && (
        <p className="error">Updates are not set up on this machine. A site server built with deploy/proxmox/create-site.sh, or a
          Pi set up by provision.sh, has them.</p>
      )}
      {offline && <p className="up-banner" role="status">The site is restarting with the update… this page carries on by itself.</p>}

      {status.kind !== "none" && (
        <div className="form" aria-label="Install a release">
          <div className="form-row">
            <label htmlFor="up-file">Release file</label>
            <input id="up-file" ref={input} type="file" accept=".tar.gz,.tgz,application/gzip"
                   onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </div>
          <p className="muted small">A release is devine-bms-&lt;version&gt;.tar.gz, made with deploy/build-release.sh. Only the file's
            shape is checked, not who made it: install releases from Devine BMS only.</p>
          <div className="actions">
            <ActionButton primary disabled={!file || running} onClick={async () => {
              if (!file) return;
              if (!window.confirm(`Install ${file.name}? The site stops for about a minute${status.kind === "pi" ? " (the Pi: about 3 minutes)" : ""}.`)) return;
              setOutcome(null);
              try {
                const r = await fetch("/api/updates/upload", { method: "POST", body: file,
                                                               headers: { "Content-Type": "application/gzip" } });
                const data = await r.json().catch(() => ({}));
                if (!r.ok || data.ok === false) throw new Error(data.error ?? data.detail ?? `the upload failed (${r.status})`);
                setOutcome({ kind: "ok", message: `Updating from ${data.from} to ${data.version}…` });
                setFile(null);
                if (input.current) input.current.value = "";
                await load();
              } catch (e) { setOutcome({ kind: "error", message: e instanceof Error ? e.message : String(e) }); }
            }}>Install</ActionButton>
          </div>
        </div>
      )}
      <OutcomeBox outcome={outcome} />

      {agent && agent.state !== "idle" && (
        <div className={`up-progress up-${agent.state}`} aria-label="Update progress">
          <h3>{agent.state === "running" ? `Updating to ${agent.version}` : agent.state === "unreachable" ? "The updater"
            : `Update to ${agent.version}`}</h3>
          {agent.state !== "running" && agent.state !== "unreachable" && <p role="status"><strong>{RESULT[agent.state]}</strong>
            {agent.reason ? ` ${agent.reason}` : ""}</p>}
          {agent.state === "unreachable" && <p className="error">{agent.reason}</p>}
          <ol>{(agent.steps ?? []).map((s, i) => <li key={i}><span className="muted small">{new Date(s.at).toLocaleTimeString()}</span> {s.text}</li>)}</ol>
        </div>
      )}
      {status.lastUpdate && (
        <p role="status">Last update ({when(status.lastUpdate.at)}): {RESULT[status.lastUpdate.state] ?? status.lastUpdate.state}
          {status.lastUpdate.reason ? ` ${status.lastUpdate.reason}` : ""}</p>
      )}

      {status.hmis && status.hmis.length > 0 && (
        <>
          <h3>Standby HMIs</h3>
          <table className="grid" aria-label="Standby HMIs">
            <thead><tr><th>HMI</th><th>Version</th><th>Last update on the HMI</th><th>Last sent</th></tr></thead>
            <tbody>
              {status.hmis.map((h) => (
                <tr key={h.url}>
                  <td>{h.name ?? "–"}<div className="mono small muted">{h.url}</div>{h.mode ? <div className="small">{h.mode}</div> : null}</td>
                  <td>{h.version ?? <span className="muted">not answering</span>}</td>
                  <td>{h.lastUpdate ? `${h.lastUpdate.version}: ${h.lastUpdate.state} (${when(h.lastUpdate.at)})` : "–"}</td>
                  <td>{h.push ? `${h.push.version}: ${h.push.result}` : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h3>History</h3>
      {history.length ? <AuditTable rows={history} /> : <p className="muted">No updates yet.</p>}
    </section>
  );
}
