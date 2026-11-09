// Devices, buses, rooms and equipment: a list and an editor (main spec 5.1).
import { useMemo, useState } from "react";
import { EquipLogicPanel } from "../logic/EquipLogicPanel";
import { EquipCounters } from "./EquipCounters";
import type { Health, Kind, Obj } from "./api";
import { getHealth, listItems } from "./api";
import { ItemEditor } from "./ItemEditor";
import { MqttLogins } from "./MqttLogins";
import { TestReadPanel } from "./TestRead";
import { Badge, useLoad } from "./ui";

export const DEVICE_SKELETONS: Record<string, Obj> = {
  modbus_tcp: { protocol: "modbus_tcp", connection: { host: "192.168.1.50", port: 502, unitId: 1, timeoutS: 3, retries: 2 } },
  modbus: { protocol: "modbus", connection: { bus: "", unitId: 1 } },
  bacnet_ip: { protocol: "bacnet_ip", connection: { address: "192.168.1.60", deviceInstance: 1000, cov: false } },
  bacnet_mstp: { protocol: "bacnet_mstp", connection: { trunk: "", mac: 2, deviceInstance: 1000 } },
  mqtt: { protocol: "mqtt", connection: { broker: "" } },
};

export const BUS_SKELETONS: Record<string, Obj> = {
  modbus_rtu: { type: "modbus_rtu", port: "/dev/ttyBMS0", baudrate: 9600, parity: "N", dataBits: 8, stopBits: 1, timeoutS: 1, retries: 1 },
  modbus_rtu_over_tcp: { type: "modbus_rtu_over_tcp", host: "192.168.1.70", port: 502, timeoutS: 1, retries: 1 },
  modbus_tcp_gateway: { type: "modbus_tcp_gateway", host: "192.168.1.70", port: 502, timeoutS: 1, retries: 1 },
  bacnet_mstp: { type: "bacnet_mstp", service: "127.0.0.1:47900", port: "/dev/ttyBMS1", baudrate: 38400, mac: 0, maxMaster: 127, maxInfoFrames: 1, deviceInstance: 3999100, timeoutS: 8, networkNumber: 101 },
  mqtt_broker: { type: "mqtt_broker", host: "127.0.0.1", port: 1883, username: "", passwordEnv: "BMS_MQTT_PASSWORD", tls: false },
};

const TITLES: Record<Kind, string> = {
  devices: "Devices",
  buses: "Buses and networks",
  spaces: "Rooms",
  equips: "Equipment",
  points: "Points",
};

function summary(kind: Kind, item: Obj): string {
  if (kind === "devices") {
    const c = item.connection ?? {};
    const where =
      c.host != null ? `${c.host}:${c.port ?? 502} unit ${c.unitId ?? 1}`
      : c.bus ? `${c.bus}, unit ${c.unitId}`
      : c.trunk ? `${c.trunk}, MAC ${c.mac}, device ${c.deviceInstance}`
      : c.address ? `${c.address}, device ${c.deviceInstance}`
      : c.broker ? `broker ${c.broker}` : "";
    return `${item.protocol} · ${where}`;
  }
  if (kind === "buses") return `${item.type} · ${item.port && item.type !== "modbus_rtu_over_tcp" ? item.port : `${item.host}:${item.port}`}`;
  if (kind === "equips") return item.template ? `${item.template.id} v${item.template.version}` : item.space ?? "";
  if (kind === "spaces") return item.template ? `${item.template.id} v${item.template.version}` : "";
  return "";
}

export function ItemsPage({ kind }: { kind: Kind }) {
  const items = useLoad(() => listItems(kind), [kind]);
  const points = useLoad(() => (kind === "devices" ? listItems("points") : Promise.resolve([] as Obj[])), [kind]);
  const health = useLoad<Health | null>(() => getHealth().catch(() => null), [kind]);
  const [selected, setSelected] = useState<string | null>(null);
  const [adding, setAdding] = useState<Obj | null>(null);
  const [newType, setNewType] = useState(kind === "devices" ? "modbus_tcp" : "modbus_rtu");
  const [filter, setFilter] = useState("");

  const list = useMemo(
    () => (items.data ?? []).filter((i) => !filter || `${i.name} ${i.dis}`.toLowerCase().includes(filter.toLowerCase())),
    [items.data, filter],
  );
  const item = adding ?? (items.data ?? []).find((i) => i.name === selected) ?? null;
  const pointCount = (device: string) => (points.data ?? []).filter((p) => p.device === device).length;

  const online = (i: Obj): boolean | null => {
    if (kind === "devices") return health.data?.devices.find((d) => d.name === i.name)?.online ?? null;
    if (kind === "buses") return health.data?.connections.find((c) => c.name === i.name)?.online ?? null;
    return null;
  };

  const startAdding = () => {
    const skeleton =
      kind === "devices" ? DEVICE_SKELETONS[newType]
      : kind === "buses" ? BUS_SKELETONS[newType]
      : kind === "equips" ? { space: null, tags: {} }
      : { tags: {} };
    setAdding({ name: "", dis: "", ...structuredClone(skeleton) });
    setSelected(null);
  };

  const reloadAll = (name?: string) => {
    setAdding(null);
    void items.reload().then(() => setSelected(name ?? null));
    void points.reload();
    void health.reload();
  };

  return (
    <div className="split">
      <section className="list-pane" aria-label={TITLES[kind]}>
        <div className="pane-head">
          <h2>{TITLES[kind]}</h2>
          <div className="row">
            {(kind === "devices" || kind === "buses") && (
              <select aria-label="Type to add" value={newType} onChange={(e) => setNewType(e.target.value)}>
                {Object.keys(kind === "devices" ? DEVICE_SKELETONS : BUS_SKELETONS).map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </select>
            )}
            <button type="button" className="btn" onClick={startAdding}>
              Add
            </button>
          </div>
        </div>
        <input className="filter" aria-label="Filter" placeholder="Filter" value={filter} onChange={(e) => setFilter(e.target.value)} />
        {items.error && <p className="error">{items.error}</p>}
        <ul className="pick-list">
          {list.map((i) => {
            const on = online(i);
            return (
              <li key={i.name}>
                <button
                  type="button"
                  className={item && !adding && item.name === i.name ? "active" : ""}
                  onClick={() => {
                    setAdding(null);
                    setSelected(i.name);
                  }}
                >
                  <span className="pick-main">
                    <span>{i.dis}</span>
                    <span className="mono muted">{i.name}</span>
                    <span className="muted small">{summary(kind, i)}</span>
                  </span>
                  <span className="pick-side">
                    {i.enabled === false && <Badge tone="muted">Disabled</Badge>}
                    {on === true && <Badge tone="ok">Online</Badge>}
                    {on === false && <Badge tone="bad">Offline</Badge>}
                    {kind === "devices" && <span className="muted small">{pointCount(i.name)} points</span>}
                  </span>
                </button>
              </li>
            );
          })}
          {!items.loading && !list.length && <li className="muted empty">Nothing here yet.</li>}
        </ul>
      </section>
      <section className="detail-pane" aria-label="Details">
        {item ? (
          <>
            <h2>{adding ? `New ${kind === "spaces" ? "room" : kind === "equips" ? "equipment" : kind.slice(0, -1)}` : item.dis}</h2>
            <ItemEditor
              key={adding ? "new" : item.name}
              kind={kind}
              item={item}
              isNew={!!adding}
              onSaved={(name) => reloadAll(name)}
              onRemoved={() => reloadAll()}
            />
            {kind === "devices" && !adding && (
              <div className="block">
                <h3>Test connection</h3>
                <p className="muted small">Reads every point of this device through its own connection, between polls. Nothing is written.</p>
                <TestReadPanel body={() => ({ device: item.name })} />
              </div>
            )}
            {kind === "buses" && !adding && <BusHealth health={health.data} name={item.name} />}
            {kind === "buses" && !adding && item.type === "mqtt_broker" && ["127.0.0.1", "localhost"].includes(String(item.host)) && <MqttLogins />}
            {kind === "equips" && !adding && <EquipLogicPanel equip={item.name} />}
            {kind === "equips" && !adding && <EquipCounters key={item.name} equip={item.name} item={item} onSaved={() => void items.reload()} />}
          </>
        ) : (
          <p className="muted">Pick one from the list, or add one.</p>
        )}
      </section>
    </div>
  );
}

function BusHealth({ health, name }: { health: Health | null; name: string }) {
  const c = health?.connections.find((x) => x.name === name);
  if (!c) return <p className="muted">Not running (disabled, or no devices use it).</p>;
  return (
    <div className="block">
      <h3>Live</h3>
      <p>
        {c.online ? <Badge tone="ok">Online</Badge> : <Badge tone="bad">Offline</Badge>} <span className="muted">{c.where}</span>
      </p>
      <p className="small">Devices: {c.devices.join(", ") || "none"}</p>
      {c.trunkStats && (
        <dl className="kv">
          {Object.entries(c.trunkStats).map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}
