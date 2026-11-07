// Setup > Standalone (milestone 11a): the log window and USB export, the state file, the point
// count against the standalone limit, and the Pi's network settings.
import { useEffect, useState } from "react";
import { getStandalone, type StandaloneStatus } from "../hmi/HmiStandalone";
import { get, post, put } from "./api";
import type { Outcome } from "./ui";
import { ActionButton, OutcomeBox, outcomeFrom } from "./ui";

interface Network { managed: boolean; addresses: string[]; mode?: string; address?: string; gateway?: string; dns?: string }

export function StandalonePage() {
  const [s, setS] = useState<StandaloneStatus | null>(null);
  const [form, setForm] = useState<{ logWindowH: string; usbEnabled: boolean; usbIntervalMin: string } | null>(null);
  const [net, setNet] = useState<Network | null>(null);
  const [netForm, setNetForm] = useState({ mode: "dhcp", address: "", gateway: "", dns: "" });
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  const load = async () => {
    const got = await getStandalone();
    setS(got);
    setForm({ logWindowH: String(got.settings.logWindowH), usbEnabled: got.settings.usbEnabled,
              usbIntervalMin: String(got.settings.usbIntervalMin) });
  };
  useEffect(() => {
    load().catch((e) => setOutcome(outcomeFrom(e)));
    get<Network>("/api/standalone/network").then((n) => {
      setNet(n);
      setNetForm({ mode: n.mode ?? "dhcp", address: n.address ?? "", gateway: n.gateway ?? "", dns: n.dns ?? "" });
    }).catch(() => undefined);
  }, []);

  if (!s || !form) return <section className="main-pane"><h2>Standalone</h2><OutcomeBox outcome={outcome} /></section>;
  const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "never");
  return (
    <section className="main-pane" aria-labelledby="sa-h">
      <h2 id="sa-h">Standalone</h2>
      <p className="muted">This Pi is the whole system. Live values and the log are kept in memory; the site's settings are saved to the
        card only when they change.</p>
      <p className={s.points > s.pointLimit ? "error" : "muted"}>
        {s.points} points ({s.loggedPoints} logged){s.points > s.pointLimit ? ` — more than the ${s.pointLimit} a standalone Pi is sized for` : ` of about ${s.pointLimit}`}.
      </p>

      <h3>Log and USB export</h3>
      <div className="form">
        <div className="form-row"><label htmlFor="sa-window">Log window (hours)</label>
          <input id="sa-window" type="number" min={1} max={168} value={form.logWindowH} onChange={(e) => setForm({ ...form, logWindowH: e.target.value })} /></div>
        <div className="form-row"><label htmlFor="sa-usb">Export to a USB stick</label>
          <input id="sa-usb" type="checkbox" checked={form.usbEnabled} onChange={(e) => setForm({ ...form, usbEnabled: e.target.checked })} /></div>
        <div className="form-row"><label htmlFor="sa-every">Export every (minutes)</label>
          <input id="sa-every" type="number" min={5} max={1440} value={form.usbIntervalMin} onChange={(e) => setForm({ ...form, usbIntervalMin: e.target.value })} /></div>
        <div className="actions">
          <ActionButton primary onClick={async () => {
            try {
              await put("/api/standalone/settings", { logWindowH: Number(form.logWindowH), usbEnabled: form.usbEnabled,
                                                       usbIntervalMin: Number(form.usbIntervalMin) });
              setOutcome({ kind: "ok", message: "Saved." });
              await load();
            } catch (e) { setOutcome(outcomeFrom(e)); }
          }}>Save</ActionButton>
          <ActionButton onClick={async () => {
            try {
              const r = await post<{ lastResult: string; lastRows: number }>("/api/standalone/export", {});
              setOutcome(r.lastResult === "ok" ? { kind: "ok", message: `Exported ${r.lastRows} minutes.` }
                : { kind: "error", message: `Export failed: ${r.lastResult}` });
              await load();
            } catch (e) { setOutcome(outcomeFrom(e)); }
          }}>Export now</ActionButton>
          <a className="btn" href="/api/standalone/log.csv?hours=72" download>Download the log (CSV)</a>
        </div>
        <p className="muted small">Last export: {when(s.usb.lastExport)}{s.usb.lastResult ? ` (${s.usb.lastResult})` : ""}
          {s.usb.nextExport ? ` · next ${when(s.usb.nextExport)}` : ""}</p>
      </div>

      <h3>Saved settings (the state file)</h3>
      <p className={s.state.lastError ? "error" : "muted"}>
        {s.state.lastError ? `The last save failed: ${s.state.lastError}` : `Last saved ${when(s.state.lastSaved)}.`}{" "}
        <span className="small">{s.state.file}</span>
      </p>
      <ActionButton onClick={async () => {
        try { await post("/api/standalone/save", {}); setOutcome({ kind: "ok", message: "Saved to the card." }); await load(); }
        catch (e) { setOutcome(outcomeFrom(e)); }
      }}>Save now</ActionButton>

      <h3>Network</h3>
      {net && <p className="muted">Addresses now: {net.addresses.join(", ") || "none"}</p>}
      {net && !net.managed && <p className="muted small">The network is set by the Pi's install (this backend has no network helper).</p>}
      {net?.managed && (
        <div className="form">
          <div className="form-row"><label htmlFor="sa-net">Address</label>
            <select id="sa-net" value={netForm.mode} onChange={(e) => setNetForm({ ...netForm, mode: e.target.value })}>
              <option value="dhcp">Automatic (DHCP)</option><option value="static">Fixed</option></select></div>
          {netForm.mode === "static" && (
            <>
              <div className="form-row"><label htmlFor="sa-addr">Address with network size</label>
                <input id="sa-addr" value={netForm.address} placeholder="192.168.1.20/24" onChange={(e) => setNetForm({ ...netForm, address: e.target.value })} /></div>
              <div className="form-row"><label htmlFor="sa-gw">Gateway</label>
                <input id="sa-gw" value={netForm.gateway} onChange={(e) => setNetForm({ ...netForm, gateway: e.target.value })} /></div>
              <div className="form-row"><label htmlFor="sa-dns">DNS servers</label>
                <input id="sa-dns" value={netForm.dns} placeholder="1.1.1.1, 8.8.8.8" onChange={(e) => setNetForm({ ...netForm, dns: e.target.value })} /></div>
            </>
          )}
          <p className="muted small">Changing the address can cut this browser off: open the new address afterwards.</p>
          <ActionButton onClick={async () => {
            if (!confirm("Apply the network change now?")) return;
            try { await put("/api/standalone/network", netForm); setOutcome({ kind: "ok", message: "Network changed." }); }
            catch (e) { setOutcome(outcomeFrom(e)); }
          }}>Apply network settings</ActionButton>
        </div>
      )}
      <OutcomeBox outcome={outcome} />
    </section>
  );
}
