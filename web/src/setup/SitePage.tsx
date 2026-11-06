// Site settings, and the whole setup as one file (export and import, main spec 3.1 and 5.1.1).
import { useEffect, useState } from "react";
import { getCommandSettings, putCommandSettings } from "../commands/commandsApi";
import type { Obj } from "./api";
import { download, getConfig, put } from "./api";
import type { Outcome } from "./ui";
import { ActionButton, FilePick, OutcomeBox, outcomeFrom, useLoad } from "./ui";

export function SitePage() {
  const cfg = useLoad(() => getConfig(), []);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [file, setFile] = useState<{ name: string; data: Obj } | null>(null);
  const [site, setSite] = useState<Obj | null>(null);
  const [bacnet, setBacnet] = useState<string | null>(null);

  const current = cfg.data?.config;
  const s = site ?? current?.site;
  const b = bacnet ?? JSON.stringify(current?.bacnet ?? null, null, 2);

  return (
    <div className="split">
      <section className="main-pane">
        <h2>Site</h2>
        {s && (
          <div className="form">
            <div className="form-row"><label htmlFor="s-name">Name</label><input id="s-name" value={s.name} readOnly /></div>
            <div className="form-row"><label htmlFor="s-dis">Display name</label><input id="s-dis" value={s.dis} onChange={(e) => setSite({ ...s, dis: e.target.value })} /></div>
            <div className="form-row"><label htmlFor="s-tz">Time zone (Haystack name)</label><input id="s-tz" value={s.tz ?? ""} onChange={(e) => setSite({ ...s, tz: e.target.value })} /></div>
            <div className="form-row">
              <label htmlFor="s-bacnet">BACnet settings (JSON; null for none)</label>
              <textarea id="s-bacnet" className="json short" value={b} onChange={(e) => setBacnet(e.target.value)} />
            </div>
            <div className="actions">
              <ActionButton primary onClick={async () => {
                try {
                  const r = await put("/api/setup/site", { site: s, bacnet: JSON.parse(b) });
                  setOutcome({ kind: "ok", message: "Saved and live.", issues: r.warnings });
                  setSite(null); setBacnet(null); await cfg.reload();
                } catch (e) { setOutcome(outcomeFrom(e)); }
              }}>Save site settings</ActionButton>
            </div>
          </div>
        )}
        <OutcomeBox outcome={outcome} />
        <OverrideTimes />
      </section>
      <aside className="side-pane" aria-label="Setup file">
        <p className="eyebrow">Setup file</p>
        <h2>Export and import</h2>
        <p className="small">Devices, points, rooms, equipment, tags and templates as one file (<code>devine-bms-config/0</code>). History is not included.</p>
        <button type="button" className="btn" disabled={!current} onClick={() => current && download(`${current.site.name}-setup.json`, JSON.stringify(current, null, 2))}>
          Export the setup
        </button>
        <hr />
        <FilePick label="Choose a setup file to import" accept=".json,application/json" onText={async (text, name) => {
          try {
            const data = JSON.parse(text);
            setFile({ name, data });
            const r = await put("/api/setup/config?check=true", data);
            setOutcome({ kind: "ok", message: `${name} passes the checks. Import replaces the whole setup: anything not in the file is disabled.`, issues: r.warnings });
          } catch (e) { setFile(null); setOutcome(outcomeFrom(e)); }
        }} />
        {file && (
          <ActionButton danger onClick={async () => {
            if (!window.confirm(`Replace the whole setup with ${file.name}?`)) return;
            try {
              const r = await put("/api/setup/config", file.data);
              setOutcome({ kind: "ok", message: `Imported ${file.name}; it is live.`, issues: r.warnings });
              setFile(null); await cfg.reload();
            } catch (e) { setOutcome(outcomeFrom(e)); }
          }}>Import {file.name}</ActionButton>
        )}
      </aside>
    </div>
  );
}

/** Hand-mode times offered to operators (main spec 5.6: 1, 2 or 8 hours, 2 by default). */
function OverrideTimes() {
  const [choices, setChoices] = useState("");
  const [def, setDef] = useState("");
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  useEffect(() => {
    getCommandSettings().then((s) => { setChoices(s.choicesH.join(", ")); setDef(String(s.defaultH)); }).catch(() => undefined);
  }, []);
  return (
    <div className="block">
      <h3>Hand-mode override times</h3>
      <p className="muted small">The times an operator can pick when overriding a point, in hours. When the time ends the point returns to auto.</p>
      <div className="form">
        <div className="form-row"><label htmlFor="o-choices">Choices (hours)</label><input id="o-choices" value={choices} onChange={(e) => setChoices(e.target.value)} /></div>
        <div className="form-row"><label htmlFor="o-default">Default (hours)</label><input id="o-default" value={def} onChange={(e) => setDef(e.target.value)} /></div>
        <div className="actions">
          <ActionButton onClick={async () => {
            try {
              const saved = await putCommandSettings({ choicesH: choices.split(/[\s,]+/).filter(Boolean).map(Number), defaultH: Number(def) });
              setChoices(saved.choicesH.join(", ")); setDef(String(saved.defaultH));
              setOutcome({ kind: "ok", message: "Saved." });
            } catch (e) { setOutcome(outcomeFrom(e)); }
          }}>Save override times</ActionButton>
        </div>
        <OutcomeBox outcome={outcome} />
      </div>
    </div>
  );
}
