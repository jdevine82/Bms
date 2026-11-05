// Discover: find devices on each network and add them (main spec 5.1.3; mockup sheet 1).
import { useEffect, useMemo, useRef, useState } from "react";
import type { Obj, Scan } from "./api";
import { addFound, getScan, listItems, listScans, startScan, stopScan } from "./api";
import { navigate } from "./nav";
import { TestReadPanel } from "./TestRead";
import { slug } from "./text";
import type { Outcome } from "./ui";
import { ActionButton, Badge, OutcomeBox, outcomeFrom, useLoad } from "./ui";
import { startDraftFromDevice } from "./wizardDrafts";

interface Network {
  key: string;
  kind: "bacnet_ip" | "bacnet_mstp" | "modbus_tcp" | "modbus_rtu";
  label: string;
  serial: boolean;
  fixed: Obj; // parameters that pick the network
}

const DEFAULTS: Record<Network["kind"], Obj> = {
  bacnet_ip: { low: "", high: "", target: "", waitS: 3 },
  bacnet_mstp: { low: "", high: "", waitMs: 3000 },
  modbus_tcp: { network: "192.168.1.0/24", port: 502, unitId: 1, timeoutS: 0.5 },
  modbus_rtu: { from: 1, to: 247 },
};

const PARAM_LABELS: Record<string, string> = {
  low: "Lowest instance",
  high: "Highest instance",
  target: "Only this address",
  waitS: "Wait (s)",
  waitMs: "Wait (ms)",
  network: "IP range (CIDR)",
  port: "Port",
  unitId: "Unit ID",
  timeoutS: "Timeout (s)",
  from: "First unit ID",
  to: "Last unit ID",
};

function address(row: Obj): string {
  if (row.protocol === "bacnet_ip") return `BACnet/IP ${row.address ?? ""} · device ${row.instance}`;
  if (row.protocol === "bacnet_mstp") return `MS/TP ${row.trunk ?? ""} MAC ${row.mac ?? "?"} · device ${row.instance}`;
  if (row.protocol === "modbus_tcp") return `Modbus TCP ${row.host}:${row.port ?? 502} · unit ${row.unitId}`;
  return `Modbus RTU ${row.bus} · unit ${row.unitId}`;
}

function reported(row: Obj): string {
  const parts = [row.vendor, row.model ?? row.productCode, row.revision ?? row.firmware].filter(Boolean);
  if (parts.length) return parts.join(" · ");
  if (row.objectName) return row.objectName;
  return row.status === "missing" ? "Did not answer" : "Unknown device";
}

const STATUS: Record<string, { tone: "ok" | "warn" | "bad" | "info" | "muted"; text: string }> = {
  new: { tone: "info", text: "New" },
  added: { tone: "ok", text: "Already added" },
  clash: { tone: "bad", text: "Clash" },
  missing: { tone: "warn", text: "Missing" },
};

/** The device a result becomes when added (as the backend's Discover add does). */
export function deviceFromResult(row: Obj, name: string, dis: string): Obj {
  const base = { name, dis: dis || name };
  if (row.protocol === "bacnet_ip") return { ...base, protocol: "bacnet_ip", connection: { address: row.address, deviceInstance: row.instance } };
  if (row.protocol === "bacnet_mstp")
    return { ...base, protocol: "bacnet_mstp", connection: { trunk: row.trunk, mac: row.mac, deviceInstance: row.instance } };
  if (row.protocol === "modbus_tcp")
    return { ...base, protocol: "modbus_tcp", connection: { host: row.host, port: row.port, unitId: row.unitId } };
  return { ...base, protocol: "modbus", connection: { bus: row.bus, unitId: row.unitId } };
}

function suggestedName(row: Obj): string {
  const model = String(row.model ?? row.productCode ?? row.objectName ?? row.vendor ?? "device");
  const at = row.instance ?? row.unitId ?? "";
  return slug(`${model}_${at}`).slice(0, 40);
}

export function DiscoverPage() {
  const buses = useLoad(() => listItems("buses"), []);
  const [scans, setScans] = useState<Record<string, Scan>>({}); // latest scan per network key
  const [params, setParams] = useState<Record<string, Obj>>({});
  const [show, setShow] = useState<string>("all");
  const [selected, setSelected] = useState<{ net: string; row: number } | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [name, setName] = useState("");
  const [dis, setDis] = useState("");
  const [added, setAdded] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  const networks = useMemo<Network[]>(() => {
    const out: Network[] = [{ key: "bacnet_ip", kind: "bacnet_ip", label: "BACnet/IP", serial: false, fixed: {} }];
    for (const b of buses.data ?? []) {
      if (b.type === "bacnet_mstp") out.push({ key: `mstp:${b.name}`, kind: "bacnet_mstp", label: `BACnet MS/TP · ${b.name}`, serial: true, fixed: { trunk: b.name } });
    }
    out.push({ key: "modbus_tcp", kind: "modbus_tcp", label: "Modbus TCP", serial: false, fixed: {} });
    for (const b of buses.data ?? []) {
      if (String(b.type).startsWith("modbus_"))
        out.push({ key: `rtu:${b.name}`, kind: "modbus_rtu", label: `Modbus RTU · ${b.name}`, serial: true, fixed: { bus: b.name } });
    }
    return out;
  }, [buses.data]);

  // Pick up scans already running or done (for example after a page reload).
  useEffect(() => {
    void listScans()
      .then(async (list) => {
        const latest: Record<string, Scan> = {};
        for (const s of list) {
          const key = s.kind === "bacnet_mstp" ? `mstp:${s.params.trunk}` : s.kind === "modbus_rtu" ? `rtu:${s.params.bus}` : s.kind;
          if (!latest[key] || latest[key].started < s.started) latest[key] = await getScan(s.id);
        }
        setScans(latest);
      })
      .catch(() => undefined);
  }, []);

  // Follow running scans: results appear as devices answer.
  useEffect(() => {
    const running = Object.entries(scans).filter(([, s]) => s.status === "running");
    if (!running.length) return;
    timer.current = window.setTimeout(async () => {
      const updates: Record<string, Scan> = {};
      for (const [key, s] of running) {
        try {
          updates[key] = await getScan(s.id);
        } catch {
          /* the scan was dropped; keep what we have */
        }
      }
      setScans((old) => ({ ...old, ...updates }));
    }, 700);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [scans]);

  const paramsFor = (n: Network) => params[n.key] ?? DEFAULTS[n.kind];

  const scan = async (n: Network) => {
    const raw = paramsFor(n);
    const body: Obj = { kind: n.kind, ...n.fixed };
    for (const [k, v] of Object.entries(raw)) {
      if (v === "" || v == null) continue;
      body[k] = typeof DEFAULTS[n.kind][k] === "number" || ["low", "high"].includes(k) ? Number(v) : v;
    }
    if (n.serial) {
      const ok = window.confirm(
        `${n.label} is a live serial bus. The scan adds traffic while it runs and never writes to a device. Go ahead?`,
      );
      if (!ok) return;
      body.confirm = true;
    }
    try {
      const started = await startScan(body);
      setScans((old) => ({ ...old, [n.key]: started }));
      setOutcome(null);
    } catch (e) {
      setOutcome(outcomeFrom(e));
    }
  };

  const rows = useMemo(() => {
    const out: { net: Network; scan: Scan; row: Obj; index: number }[] = [];
    for (const n of networks) {
      const s = scans[n.key];
      if (!s || (show !== "all" && show !== n.key)) continue;
      s.results.forEach((row, index) => out.push({ net: n, scan: s, row, index }));
    }
    return out;
  }, [networks, scans, show]);

  const current = selected ? rows.find((r) => r.net.key === selected.net && r.index === selected.row) ?? null : null;

  useEffect(() => {
    if (current) {
      setName(suggestedName(current.row));
      setDis([current.row.vendor, current.row.model ?? current.row.productCode].filter(Boolean).join(" ") || "");
      setAdded(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.net, selected?.row]);

  const count = (key: string) => scans[key]?.results.filter((r) => r.status !== "missing").length ?? 0;

  return (
    <div className="three">
      <nav className="side-list" aria-label="Networks">
        <h3>Networks</h3>
        <button type="button" className={show === "all" ? "active" : ""} onClick={() => setShow("all")}>
          All networks <span>{networks.reduce((a, n) => a + count(n.key), 0)}</span>
        </button>
        {networks.map((n) => (
          <button key={n.key} type="button" className={show === n.key ? "active" : ""} onClick={() => setShow(n.key)}>
            {n.label} <span>{count(n.key)}</span>
          </button>
        ))}
      </nav>
      <section className="main-pane">
        <div className="pane-head">
          <div>
            <h2>Discover devices</h2>
            <p className="muted small">Scans only read, stay inside the range you give, and are recorded in the change log.</p>
          </div>
          <ActionButton
            onClick={async () => {
              for (const n of networks.filter((x) => !x.serial)) await scan(n);
            }}
          >
            Scan IP networks
          </ActionButton>
        </div>
        <OutcomeBox outcome={outcome} />
        <div className="scan-rows">
          {networks
            .filter((n) => show === "all" || show === n.key)
            .map((n) => {
              const s = scans[n.key];
              const p = paramsFor(n);
              return (
                <div key={n.key} className="scan-row" data-network={n.key}>
                  <div className="scan-title">
                    <strong>{n.label}</strong>
                    {n.serial && <Badge tone="warn">Live serial bus</Badge>}
                    <span className="muted small scan-progress">
                      {!s ? "Not scanned" : s.status === "running"
                        ? `Scanning… ${s.progress.total ? Math.round((100 * s.progress.done) / s.progress.total) : 0}%, ${s.results.length} found`
                        : s.status === "failed" ? `Failed: ${s.error}`
                        : `${s.status === "stopped" ? "Stopped" : "Done"}: ${s.results.filter((r) => r.status !== "missing").length} found`}
                    </span>
                  </div>
                  {s?.status === "running" && (
                    <progress value={s.progress.done} max={Math.max(1, s.progress.total)} aria-label={`${n.label} progress`} />
                  )}
                  <div className="scan-params">
                    {Object.keys(DEFAULTS[n.kind]).map((k) => (
                      <label key={k}>
                        <span>{PARAM_LABELS[k] ?? k}</span>
                        <input
                          value={p[k] ?? ""}
                          onChange={(e) => setParams((old) => ({ ...old, [n.key]: { ...paramsFor(n), [k]: e.target.value } }))}
                        />
                      </label>
                    ))}
                    {s?.status === "running" ? (
                      <ActionButton danger onClick={async () => {
                        const stopped = await stopScan(s.id);
                        setScans((o) => ({ ...o, [n.key]: stopped }));
                      }}>
                        Stop
                      </ActionButton>
                    ) : (
                      <ActionButton primary onClick={() => scan(n)}>
                        Scan
                      </ActionButton>
                    )}
                  </div>
                </div>
              );
            })}
        </div>
        <table className="grid" aria-label="Results">
          <thead>
            <tr>
              <th>Status</th>
              <th>Network and address</th>
              <th>Reported by device</th>
              <th>Suggested template</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ net, row, index }) => {
              const st = STATUS[row.status] ?? { tone: "muted" as const, text: "Answered" };
              const active = selected?.net === net.key && selected.row === index;
              return (
                <tr
                  key={`${net.key}-${index}`}
                  className={`clickable ${active ? "selected" : ""} ${row.status === "clash" ? "row-bad" : ""}`}
                  onClick={() => setSelected({ net: net.key, row: index })}
                >
                  <td>
                    <Badge tone={st.tone}>{st.text}</Badge>
                  </td>
                  <td>{address(row)}</td>
                  <td>{reported(row)}{row.device ? <span className="muted small"> · {row.device}</span> : null}</td>
                  <td>{row.templates?.length ? `✓ ${row.templates.join(", ")}` : <span className="muted">Choose template</span>}</td>
                </tr>
              );
            })}
            {!rows.length && (
              <tr>
                <td colSpan={4} className="muted">No results yet. Scan a network.</td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
      <aside className="side-pane" aria-label="Selected result">
        {current ? (
          <>
            <p className="eyebrow">Selected result</p>
            <h2>{reported(current.row)}</h2>
            <p className="muted">{address(current.row)}</p>
            {current.row.status === "clash" && (
              <p className="note bad">Two devices answer with the same device instance. Change one of them on the device before adding it.</p>
            )}
            {current.row.status === "added" && <p className="note">Already added as {current.row.device}.</p>}
            {current.row.status === "missing" && <p className="note warn">{current.row.device} is set up here but did not answer.</p>}
            {current.net.serial && <p className="note warn">Live serial bus: test reads add traffic between polls.</p>}
            {(current.row.status === "new" || current.row.status === undefined) && (
              <>
                <label className="stack">
                  <span>Device name</span>
                  <input value={name} onChange={(e) => setName(e.target.value)} />
                </label>
                <label className="stack">
                  <span>Display name</span>
                  <input value={dis} onChange={(e) => setDis(e.target.value)} />
                </label>
                <div className="actions column">
                  <ActionButton
                    primary
                    disabled={!!added}
                    onClick={async () => {
                      try {
                        const result = await addFound(current.scan.id, [{ row: current.index, name, dis }]);
                        if (result.failed.length) {
                          setOutcome({ kind: "error", message: result.failed[0].error, issues: result.failed[0].issues });
                          return;
                        }
                        setAdded(name);
                        setOutcome({ kind: "ok", message: `Added ${name}. It has no points yet: give it a template next.` });
                        setScans((o) => ({ ...o, [current.net.key]: { ...o[current.net.key], results: o[current.net.key].results.map((r, i) => (i === current.index ? { ...r, status: "added", device: name } : r)) } }));
                      } catch (e) {
                        setOutcome(outcomeFrom(e));
                      }
                    }}
                  >
                    Add device
                  </ActionButton>
                  <button
                    type="button"
                    className="btn"
                    onClick={() => {
                      startDraftFromDevice(deviceFromResult(current.row, name, dis), current.row.templates?.[0] ?? null);
                      navigate("commission");
                    }}
                  >
                    Commission with a template…
                  </button>
                </div>
              </>
            )}
            {(added || current.row.status === "added") && (
              <div className="block">
                <TestReadPanel body={() => ({ device: added ?? current.row.device })} />
              </div>
            )}
            <details>
              <summary>Everything the device reported</summary>
              <pre className="small">{JSON.stringify(current.row, null, 2)}</pre>
            </details>
          </>
        ) : (
          <p className="muted">Pick a result to see it and add it.</p>
        )}
      </aside>
    </div>
  );
}
