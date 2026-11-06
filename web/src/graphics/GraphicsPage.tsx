// The Graphics page (web app) and Graphics tab (HMI): a tab strip with Site first, then the room
// and section graphics in their tab order (main spec 6.5). Tapping a symbol with a command opens
// the same command panel as the Points page; on the HMI it needs the PIN first.
import { useEffect, useMemo, useState } from "react";
import type { LiveValue, Point, Site } from "../api";
import { useAuth } from "../auth/AuthGate";
import { useOpenAlarms } from "../alarms/useAlarms";
import { CommandPanel } from "../commands/CommandPanel";
import { PinPad } from "../hmi/HmiSetpoints";
import type { Graphic, LibrarySymbol } from "./graphicsApi";
import { getGraphics, getLibrary } from "./graphicsApi";
import { GraphicView } from "./GraphicView";

interface Props {
  site: Site;
  points: Point[];
  live: Record<string, LiveValue>;
  hmi?: boolean;
  selected?: string | null; // the web app keeps the open tab in the address
  onSelect?: (name: string) => void;
}

export function GraphicsPage({ site, points, live, hmi = false, selected, onSelect }: Props) {
  const { user, can, refresh } = useAuth();
  const [graphics, setGraphics] = useState<Graphic[] | null>(null);
  const [source, setSource] = useState<"setup" | "generated">("setup");
  const [library, setLibrary] = useState<Record<string, LibrarySymbol>>({});
  const [error, setError] = useState<string | null>(null);
  const [own, setOwn] = useState<string | null>(null);
  const [commanding, setCommanding] = useState<string | null>(null);
  const [unlocking, setUnlocking] = useState(false);
  const { alarms } = useOpenAlarms();

  useEffect(() => {
    let stop = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const load = () =>
      Promise.all([getGraphics(), getLibrary()])
        .then(([g, lib]) => {
          if (stop) return;
          setGraphics(g.graphics);
          setSource(g.source);
          setLibrary(Object.fromEntries(lib.map((s) => [s.id, s])));
          setError(null);
        })
        .catch((e: unknown) => {
          if (stop) return;
          setError(String(e));
          retry = setTimeout(load, 5000);
        });
    load();
    return () => {
      stop = true;
      clearTimeout(retry);
    };
  }, []);

  const byName = useMemo(() => Object.fromEntries(points.map((p) => [p.name, p])), [points]);
  const alarmed = useMemo(
    () => new Set(alarms.filter((a) => a.active && a.key.startsWith("point:")).map((a) => a.key.split(":")[1])),
    [alarms],
  );
  const spaces = useMemo(() => Object.fromEntries((site.spaces ?? []).map((s) => [s.name, s.dis])), [site]);
  const tabs = (graphics ?? []).filter((g) => g.tab);
  const current = selected ?? own;
  const shown = tabs.find((g) => g.name === current) ?? tabs[0] ?? null;
  const select = (name: string) => (onSelect ? onSelect(name) : setOwn(name));
  const canCommand = can("operator") && (!!user.username || hmi);
  const unlocked = !!user.username && can("operator");
  const target = points.find((p) => p.id === commanding) ?? null;

  const openRoom = (space: string) => {
    const g = tabs.find((x) => x.space === space && x.kind === "room");
    if (g) select(g.name);
  };
  const command = (p: Point) => {
    if (hmi && !unlocked) setUnlocking(true);
    setCommanding(p.id);
  };

  if (hmi && unlocking && !unlocked) {
    return <PinPad onDone={async () => { setUnlocking(false); await refresh(); }} />;
  }

  return (
    <section className={`graphics-page${hmi ? " hmi-graphics" : ""}`} aria-label="Graphics">
      {error && <p className="error">Cannot load the graphics: {error}</p>}
      {graphics && tabs.length === 0 && <p className="muted">No graphics yet.</p>}
      {tabs.length > 0 && (
        <div className="graphic-tabs" role="tablist" aria-label="Graphics">
          {tabs.map((g) => (
            <button key={g.name} type="button" role="tab" aria-selected={g === shown}
                    className={g === shown ? "active" : ""} onClick={() => select(g.name)}>
              {g.dis}
            </button>
          ))}
        </div>
      )}
      {shown && (
        <div className="graphic-frame" role="tabpanel" aria-label={shown.dis}>
          <GraphicView graphic={shown} library={library} points={byName} live={live} alarmed={alarmed}
                       spaces={spaces} onRoom={openRoom} onCommand={canCommand ? command : undefined} />
        </div>
      )}
      {!hmi && source === "generated" && graphics && can("admin") && (
        <p className="muted small">
          These starting graphics are made from the rooms and equipment. Changing one in Setup saves them.
        </p>
      )}
      {target && (!hmi || unlocked) && (
        <div className="command-overlay" onClick={(e) => e.target === e.currentTarget && setCommanding(null)}>
          <CommandPanel point={target} live={live[target.id] ?? target.cur} tz={site.ianaTz} big={hmi}
                        onClose={() => setCommanding(null)} />
        </div>
      )}
    </section>
  );
}
