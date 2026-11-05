// Edit one setup item (a device, bus, room or piece of equipment) as a form or as JSON.
// Every save is checked by the backend with the config file's checks first (milestone 5A).
import { useEffect, useState } from "react";
import type { Kind, Obj } from "./api";
import { deleteItem, putItem, renameItem } from "./api";
import { tagsToText, textToTags } from "./text";
import type { Outcome } from "./ui";
import { ActionButton, OutcomeBox, outcomeFrom } from "./ui";

const LABELS: Record<string, string> = {
  dis: "Display name",
  host: "Host",
  port: "Port",
  unitId: "Unit ID",
  timeoutS: "Timeout (s)",
  retries: "Retries",
  blockSize: "Block size",
  bus: "Bus",
  trunk: "MS/TP trunk",
  broker: "MQTT broker",
  mac: "MAC address",
  deviceInstance: "Device instance",
  address: "Address",
  baudrate: "Baud rate",
  parity: "Parity",
  dataBits: "Data bits",
  stopBits: "Stop bits",
  enabled: "Enabled",
  space: "Room",
  parent: "Part of",
  passwordEnv: "Password environment variable",
  username: "User name",
  cov: "Use COV",
  covLifetimeS: "COV lifetime (s)",
  service: "Service address",
  maxMaster: "Max master",
  maxInfoFrames: "Max info frames",
  keepaliveS: "Keep-alive (s)",
  clientId: "Client ID",
  tls: "TLS",
  availabilityTopic: "Availability topic",
  availabilityOnline: "Online payload",
  availabilityOffline: "Offline payload",
};

const label = (key: string) => LABELS[key] ?? key;

function Field({ path, value, onChange }: { path: string[]; value: any; onChange: (v: any) => void }) {
  const key = path[path.length - 1];
  const id = `f-${path.join("-")}`;
  if (key === "tags") {
    return (
      <div className="form-row">
        <label htmlFor={id}>Tags</label>
        <input id={id} defaultValue={tagsToText(value)} onBlur={(e) => onChange(textToTags(e.target.value))} />
      </div>
    );
  }
  if (typeof value === "boolean") {
    return (
      <div className="form-row check">
        <label>
          <input type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)} /> {label(key)}
        </label>
      </div>
    );
  }
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    return (
      <fieldset>
        <legend>{key === "connection" ? "Connection" : key === "template" ? "Template" : label(key)}</legend>
        {Object.entries(value).map(([k, v]) => (
          <Field key={k} path={[...path, k]} value={v} onChange={(nv) => onChange({ ...value, [k]: nv })} />
        ))}
      </fieldset>
    );
  }
  if (Array.isArray(value)) {
    return (
      <div className="form-row">
        <label htmlFor={id}>{label(key)}</label>
        <input
          id={id}
          defaultValue={JSON.stringify(value)}
          onBlur={(e) => {
            try {
              onChange(JSON.parse(e.target.value));
            } catch {
              /* left as it was; the JSON view shows the whole item */
            }
          }}
        />
      </div>
    );
  }
  return (
    <div className="form-row">
      <label htmlFor={id}>{label(key)}</label>
      <input
        id={id}
        type={typeof value === "number" ? "number" : "text"}
        step="any"
        value={value ?? ""}
        onChange={(e) =>
          onChange(typeof value === "number" ? (e.target.value === "" ? null : Number(e.target.value)) : e.target.value)
        }
      />
    </div>
  );
}

export function ItemEditor({
  kind,
  item,
  isNew,
  onSaved,
  onRemoved,
}: {
  kind: Kind;
  item: Obj;
  isNew: boolean;
  onSaved: (name: string) => void;
  onRemoved: () => void;
}) {
  const [draft, setDraft] = useState<Obj>(item);
  const [mode, setMode] = useState<"form" | "json">("form");
  const [jsonText, setJsonText] = useState(JSON.stringify(item, null, 2));
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [newName, setNewName] = useState("");

  useEffect(() => {
    setDraft(item);
    setJsonText(JSON.stringify(item, null, 2));
    setOutcome(null);
  }, [item]);

  const current = (): Obj | null => {
    if (mode === "form") return draft;
    try {
      return JSON.parse(jsonText) as Obj;
    } catch (e) {
      setOutcome({ kind: "error", message: `The JSON is not valid: ${String(e)}` });
      return null;
    }
  };

  const save = async (check: boolean) => {
    const value = current();
    if (!value) return;
    const name = String(value.name ?? "");
    try {
      const result = await putItem(kind, name, value, check);
      setOutcome({
        kind: "ok",
        message: check ? "The checks pass. Nothing was saved yet." : "Saved and live.",
        issues: result.warnings,
      });
      if (!check) onSaved(name);
    } catch (e) {
      setOutcome(outcomeFrom(e));
    }
  };

  const { name: itemName, ...rest } = draft;
  return (
    <div className="editor">
      <div className="seg" role="tablist" aria-label="Edit as">
        <button type="button" role="tab" aria-selected={mode === "form"} onClick={() => {
          if (mode === "json") {
            const v = current();
            if (!v) return;
            setDraft(v);
          }
          setMode("form");
        }}>
          Form
        </button>
        <button type="button" role="tab" aria-selected={mode === "json"} onClick={() => {
          setJsonText(JSON.stringify(draft, null, 2));
          setMode("json");
        }}>
          JSON
        </button>
      </div>
      {mode === "form" ? (
        <div className="form">
          <div className="form-row">
            <label htmlFor="f-name">Name</label>
            <input
              id="f-name"
              value={itemName ?? ""}
              readOnly={!isNew}
              title={isNew ? "" : "Use Rename to change a name: it keeps the history and the old name as an alias"}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            />
          </div>
          {Object.entries(rest).map(([k, v]) => (
            <Field key={k} path={[k]} value={v} onChange={(nv) => setDraft({ ...draft, [k]: nv })} />
          ))}
        </div>
      ) : (
        <textarea className="json" aria-label="Item as JSON" spellCheck={false} value={jsonText}
          onChange={(e) => setJsonText(e.target.value)} />
      )}
      <div className="actions">
        <ActionButton onClick={() => save(true)}>Check</ActionButton>
        <ActionButton primary onClick={() => save(false)}>
          {isNew ? "Add" : "Save"}
        </ActionButton>
      </div>
      {!isNew && (
        <div className="actions secondary">
          <input aria-label="New name" placeholder="new_name" value={newName} onChange={(e) => setNewName(e.target.value)} />
          <ActionButton
            disabled={!newName}
            onClick={async () => {
              try {
                await renameItem(kind, String(item.name), newName);
                setOutcome({ kind: "ok", message: `Renamed to ${newName}.` });
                setNewName("");
                onSaved(newName);
              } catch (e) {
                setOutcome(outcomeFrom(e));
              }
            }}
          >
            Rename
          </ActionButton>
          <ActionButton
            danger
            onClick={async () => {
              const what = kind === "devices" || kind === "points" ? "disable it (its history is kept)" : "remove it";
              if (!window.confirm(`${item.name}: ${what}?`)) return;
              try {
                await deleteItem(kind, String(item.name));
                onRemoved();
              } catch (e) {
                setOutcome(outcomeFrom(e));
              }
            }}
          >
            Remove
          </ActionButton>
        </div>
      )}
      <OutcomeBox outcome={outcome} />
    </div>
  );
}
