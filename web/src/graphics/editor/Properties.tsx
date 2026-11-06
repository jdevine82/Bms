// The editor's properties panel: the selected item, or the graphic itself when nothing is
// selected. Points are picked by name (the list shows display names); the setup checks run on
// Check and Save.
import { useId, useState } from "react";
import type { Point, Site } from "../../api";
import { post } from "../../setup/api";
import { SetupError } from "../../setup/api";
import type { Colour, ColourRule, Graphic, GraphicItem, LibrarySymbol, ValueBand } from "../graphicsApi";

const COLOURS: Colour[] = ["grey", "blue", "cyan", "green", "amber", "red"];
const num = (v: string): number | undefined => (v.trim() === "" || Number.isNaN(Number(v)) ? undefined : Number(v));

interface Props {
  graphic: Graphic;
  item: GraphicItem | null;
  points: Point[];
  library: LibrarySymbol[];
  site: Site | null;
  isNew: boolean;
  onGraphic: (g: Graphic) => void;
  onItem: (i: GraphicItem) => void;
  onRemove: () => void;
  onDuplicate: () => void;
  onError: (message: string) => void;
}

export function Properties(props: Props) {
  const { item } = props;
  return (
    <aside className="editor-props" aria-label="Properties">
      {item ? <ItemProps {...props} item={item} /> : <GraphicProps {...props} />}
    </aside>
  );
}

function Field({ label, children, hint }: { label: string; children: (id: string) => React.ReactNode; hint?: string }) {
  const id = useId();
  return (
    <div className="prop">
      <label htmlFor={id}>{label}</label>
      {children(id)}
      {hint && <span className="muted small">{hint}</span>}
    </div>
  );
}

function PointField({ label, value, points, onChange, numbers = false, writable = false }: {
  label: string; value: string | undefined; points: Point[]; onChange: (v: string | undefined) => void;
  numbers?: boolean; writable?: boolean;
}) {
  const listId = useId();
  const choices = points.filter((p) => (!numbers || p.kind === "Number") && (!writable || p.writable));
  const known = points.find((p) => p.name === value);
  return (
    <Field label={label} hint={value ? (known ? known.dis : "No such point") : undefined}>
      {(id) => (
        <>
          <input id={id} list={listId} value={value ?? ""} placeholder="(none)" autoComplete="off"
                 onChange={(e) => onChange(e.target.value.trim() || undefined)} />
          <datalist id={listId}>
            {choices.map((p) => <option key={p.id} value={p.name}>{p.dis}</option>)}
          </datalist>
        </>
      )}
    </Field>
  );
}

function ColourSelect({ value, onChange, label = "Colour" }: { value: Colour; onChange: (c: Colour) => void; label?: string }) {
  return (
    <Field label={label}>
      {(id) => (
        <select id={id} value={value} onChange={(e) => onChange(e.target.value as Colour)}>
          {COLOURS.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      )}
    </Field>
  );
}

function GraphicProps({ graphic: g, site, isNew, onGraphic, onError }: Props) {
  const [removed, setRemoved] = useState<string[] | null>(null);
  const set = (patch: Partial<Graphic>) => onGraphic({ ...g, ...patch });
  const importBackground = async (file: File) => {
    try {
      const r = await post<{ svg: string; viewBox: string; removed: string[] }>("/api/setup/graphics/sanitize",
        { svg: await file.text(), purpose: "background", prefix: `bg-${g.name || "new"}` });
      setRemoved(r.removed);
      set({ background: { svg: r.svg, x: 0, y: 0, w: g.width ?? 1000, h: g.height ?? 600, opacity: 0.6 } });
    } catch (e) {
      onError(e instanceof SetupError ? e.message : String(e));
    }
  };
  return (
    <>
      <h3>Graphic</h3>
      <Field label="Name" hint={isNew ? "Lowercase letters, digits and _; cannot be changed later." : undefined}>
        {(id) => <input id={id} value={g.name} readOnly={!isNew} onChange={(e) => set({ name: e.target.value })} />}
      </Field>
      <Field label="Display name">{(id) => <input id={id} value={g.dis} onChange={(e) => set({ dis: e.target.value })} />}</Field>
      <Field label="Kind">
        {(id) => (
          <select id={id} value={g.kind} onChange={(e) => set({ kind: e.target.value as Graphic["kind"] })}>
            <option value="site">Site</option><option value="room">Room</option><option value="system">System</option>
          </select>
        )}
      </Field>
      <Field label="Room">
        {(id) => (
          <select id={id} value={g.space ?? ""} onChange={(e) => set({ space: e.target.value || undefined })}>
            <option value="">(none)</option>
            {(site?.spaces ?? []).map((s) => <option key={s.name} value={s.name}>{s.dis}</option>)}
          </select>
        )}
      </Field>
      <div className="prop-row">
        <Field label="Width">{(id) => <input id={id} type="number" min={100} step={10} value={g.width ?? 1000}
                                              onChange={(e) => set({ width: num(e.target.value) ?? 1000 })} />}</Field>
        <Field label="Height">{(id) => <input id={id} type="number" min={100} step={10} value={g.height ?? 600}
                                               onChange={(e) => set({ height: num(e.target.value) ?? 600 })} />}</Field>
      </div>
      <Field label="Tab" hint="Tabs and their order can also be set in one step on the Graphics list.">
        {(id) => (
          <span className="prop-inline">
            <input id={id} type="checkbox" checked={!!g.tab}
                   onChange={(e) => set({ tab: e.target.checked ? { order: 900 } : undefined })} />
            {g.tab && <input type="number" aria-label="Tab order" value={g.tab.order} min={0} step={10}
                             onChange={(e) => set({ tab: { order: num(e.target.value) ?? 0 } })} />}
          </span>
        )}
      </Field>
      <h3>Background</h3>
      {g.background ? (
        <>
          <Field label="Opacity">
            {(id) => <input id={id} type="range" min={0.05} max={1} step={0.05} value={g.background!.opacity ?? 1}
                            onChange={(e) => set({ background: { ...g.background!, opacity: Number(e.target.value) } })} />}
          </Field>
          <div className="prop-row two">
            {(["x", "y", "w", "h"] as const).map((k) => (
              <Field key={k} label={k.toUpperCase()}>
                {(id) => <input id={id} type="number" step={10} value={g.background![k] ?? 0}
                                onChange={(e) => set({ background: { ...g.background!, [k]: num(e.target.value) ?? 0 } })} />}
              </Field>
            ))}
          </div>
          <button type="button" className="btn danger" onClick={() => { set({ background: undefined }); setRemoved(null); }}>
            Remove background
          </button>
        </>
      ) : (
        <Field label="Import an SVG background" hint="For example a floor plan. It is cleaned on the server first (1 MB at most).">
          {(id) => <input id={id} type="file" accept=".svg,image/svg+xml"
                          onChange={(e) => e.target.files?.[0] && void importBackground(e.target.files[0])} />}
        </Field>
      )}
      {removed && (
        <p className="muted small" role="status">
          {removed.length ? `Removed from the file: ${removed.join(", ")}.` : "Nothing needed removing."}
        </p>
      )}
    </>
  );
}

function ItemProps({ graphic, item, points, library, site, onItem, onRemove, onDuplicate }: Props & { item: GraphicItem }) {
  const set = (patch: Partial<GraphicItem>) => onItem({ ...item, ...patch } as GraphicItem);
  const title = { symbol: "Symbol", value: "Live value", pipe: "Pipe", duct: "Duct", room: "Room", text: "Text" }[item.type];
  return (
    <>
      <h3>{title} <code className="small">{item.id}</code></h3>
      {item.type === "symbol" && (
        <>
          <Field label="Symbol">
            {(id) => (
              <select id={id} value={item.symbol} onChange={(e) => set({ symbol: e.target.value })}>
                {library.map((s) => <option key={s.id} value={s.id}>{s.dis}</option>)}
              </select>
            )}
          </Field>
          <Field label="Label">{(id) => <input id={id} value={item.label ?? ""} onChange={(e) => set({ label: e.target.value || undefined })} />}</Field>
          <div className="prop-row">
            <Field label="Size">{(id) => <input id={id} type="number" min={20} step={10} value={item.size ?? 64}
                                                 onChange={(e) => set({ size: num(e.target.value) ?? 64 })} />}</Field>
            <Field label="Rotate">
              {(id) => (
                <select id={id} value={item.rotate ?? 0} onChange={(e) => set({ rotate: Number(e.target.value) })}>
                  {[0, 90, 180, 270].map((r) => <option key={r} value={r}>{r}°</option>)}
                </select>
              )}
            </Field>
          </div>
          <h4>Points</h4>
          <PointField label="Run (On or Off)" value={item.bind?.run} points={points} onChange={(v) => set({ bind: { ...item.bind, run: v } })} />
          <PointField label="Value (reading, position or level)" value={item.bind?.value} points={points}
                      onChange={(v) => set({ bind: { ...item.bind, value: v } })} />
          <PointField label="Fault" value={item.bind?.fault} points={points} onChange={(v) => set({ bind: { ...item.bind, fault: v } })} />
          <PointField label="Command (opened by tapping)" value={item.bind?.command} points={points} writable
                      onChange={(v) => set({ bind: { ...item.bind, command: v } })} />
        </>
      )}
      {item.type === "value" && (
        <>
          <PointField label="Point" value={item.point} points={points} onChange={(v) => set({ point: v ?? "" })} />
          <Field label="Label">{(id) => <input id={id} value={item.label ?? ""} onChange={(e) => set({ label: e.target.value || undefined })} />}</Field>
        </>
      )}
      {item.type === "text" && (
        <>
          <Field label="Text">{(id) => <input id={id} value={item.text} onChange={(e) => set({ text: e.target.value })} />}</Field>
          <Field label="Size">{(id) => <input id={id} type="number" min={8} value={item.size ?? 16}
                                               onChange={(e) => set({ size: num(e.target.value) ?? 16 })} />}</Field>
        </>
      )}
      {(item.type === "pipe" || item.type === "duct") && (
        <>
          <Field label="Width">{(id) => <input id={id} type="number" min={1} value={item.width ?? (item.type === "duct" ? 14 : 6)}
                                                onChange={(e) => set({ width: num(e.target.value) })} />}</Field>
          <RuleEditor rule={item.rule} graphic={graphic} points={points} onChange={(rule) => set({ rule })} />
        </>
      )}
      {item.type === "room" && (
        <>
          <Field label="Room">
            {(id) => (
              <select id={id} value={item.space} onChange={(e) => set({ space: e.target.value })}>
                {!(site?.spaces ?? []).some((s) => s.name === item.space) && <option value={item.space}>{item.space || "(choose)"}</option>}
                {(site?.spaces ?? []).map((s) => <option key={s.name} value={s.name}>{s.dis}</option>)}
              </select>
            )}
          </Field>
          {[0, 1, 2, 3].map((i) => (
            <PointField key={i} label={`Value ${i + 1}`} value={item.values?.[i]} points={points}
                        onChange={(v) => {
                          const values = [...(item.values ?? [])];
                          values[i] = v ?? "";
                          set({ values: values.filter(Boolean) });
                        }} />
          ))}
          <RuleEditor rule={item.rule} graphic={graphic} points={points} bandsOnly
                      onChange={(rule) => set({ rule: rule as Extract<ColourRule, { type: "bands" }> | undefined })} />
        </>
      )}
      <div className="prop-actions">
        <button type="button" className="btn" onClick={onDuplicate}>Duplicate</button>
        <button type="button" className="btn danger" onClick={onRemove}>Remove</button>
      </div>
    </>
  );
}

function RuleEditor({ rule, graphic, points, onChange, bandsOnly = false }: {
  rule: ColourRule | undefined; graphic: Graphic; points: Point[]; onChange: (r: ColourRule | undefined) => void; bandsOnly?: boolean;
}) {
  const symbols = graphic.items.filter((i) => i.type === "symbol");
  const pick = (type: string) => {
    if (type === "") onChange(undefined);
    else if (type === "follows") onChange({ type: "follows", item: symbols[0]?.id ?? "", colour: "blue" });
    else if (type === "bands") onChange({ type: "bands", point: "", hysteresis: 0.5, bands: [
      { below: 18, colour: "blue" }, { below: 24, colour: "green" }, { below: 28, colour: "amber" }, { colour: "red" }] });
    else onChange({ type: "fill", point: "", colour: "blue", min: 0, max: 100 });
  };
  const setBands = (bands: ValueBand[]) => rule?.type === "bands" && onChange({ ...rule, bands });
  return (
    <>
      <h4>Colour rule</h4>
      <Field label="Rule">
        {(id) => (
          <select id={id} value={rule?.type ?? ""} onChange={(e) => pick(e.target.value)}>
            <option value="">None (grey)</option>
            {!bandsOnly && <option value="follows">Follows a symbol</option>}
            <option value="bands">Value bands</option>
            {!bandsOnly && <option value="fill">Proportional fill</option>}
          </select>
        )}
      </Field>
      {rule?.type === "follows" && (
        <>
          <Field label="Symbol">
            {(id) => (
              <select id={id} value={rule.item} onChange={(e) => onChange({ ...rule, item: e.target.value })}>
                {!symbols.some((s) => s.id === rule.item) && <option value={rule.item}>{rule.item || "(choose)"}</option>}
                {symbols.map((s) => <option key={s.id} value={s.id}>{s.type === "symbol" ? s.label ?? s.id : s.id}</option>)}
              </select>
            )}
          </Field>
          <ColourSelect label="Colour when On" value={rule.colour ?? "green"} onChange={(colour) => onChange({ ...rule, colour })} />
        </>
      )}
      {rule?.type === "bands" && (
        <>
          <PointField label="Point" value={rule.point} points={points} numbers onChange={(v) => onChange({ ...rule, point: v ?? "" })} />
          <Field label="Hysteresis">
            {(id) => <input id={id} type="number" min={0} step={0.1} value={rule.hysteresis ?? 0}
                            onChange={(e) => onChange({ ...rule, hysteresis: num(e.target.value) ?? 0 })} />}
          </Field>
          <table className="bands" aria-label="Bands">
            <thead><tr><th>Below</th><th>Colour</th><th>Flow</th><th /></tr></thead>
            <tbody>
              {rule.bands.map((b, i) => {
                const last = i === rule.bands.length - 1;
                const change = (patch: Partial<ValueBand>) => setBands(rule.bands.map((x, j) => (j === i ? { ...x, ...patch } : x)));
                return (
                  <tr key={i}>
                    <td>{last ? <span className="muted">above</span> : (
                      <input type="number" aria-label={`Band ${i + 1} below`} value={b.below ?? ""}
                             onChange={(e) => change({ below: num(e.target.value) })} />)}</td>
                    <td>
                      <select aria-label={`Band ${i + 1} colour`} value={b.colour} onChange={(e) => change({ colour: e.target.value as Colour })}>
                        {COLOURS.map((c) => <option key={c} value={c}>{c}</option>)}
                      </select>
                    </td>
                    <td><input type="checkbox" aria-label={`Band ${i + 1} flows`} checked={b.flow !== false}
                               onChange={(e) => change({ flow: e.target.checked })} /></td>
                    <td>{rule.bands.length > 1 && (
                      <button type="button" className="link" aria-label={`Remove band ${i + 1}`}
                              onClick={() => {
                                const bands = rule.bands.filter((_, j) => j !== i);
                                bands[bands.length - 1] = { ...bands[bands.length - 1], below: undefined };
                                setBands(bands);
                              }}>×</button>)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {rule.bands.length < 4 && (
            <button type="button" className="link" onClick={() => {
              const bands = [...rule.bands];
              const lastBelow = bands.length > 1 ? bands[bands.length - 2].below ?? 0 : 0;
              bands[bands.length - 1] = { ...bands[bands.length - 1], below: lastBelow + 5 };
              setBands([...bands, { colour: "red" }]);
            }}>Add a band</button>
          )}
        </>
      )}
      {rule?.type === "fill" && (
        <>
          <PointField label="Point (0 to 100 %)" value={rule.point} points={points} numbers onChange={(v) => onChange({ ...rule, point: v ?? "" })} />
          <div className="prop-row">
            <Field label="Min">{(id) => <input id={id} type="number" value={rule.min ?? 0} onChange={(e) => onChange({ ...rule, min: num(e.target.value) ?? 0 })} />}</Field>
            <Field label="Max">{(id) => <input id={id} type="number" value={rule.max ?? 100} onChange={(e) => onChange({ ...rule, max: num(e.target.value) ?? 100 })} />}</Field>
          </div>
          <ColourSelect value={rule.colour ?? "blue"} onChange={(colour) => onChange({ ...rule, colour })} />
        </>
      )}
    </>
  );
}
