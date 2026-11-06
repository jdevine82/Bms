import { useMemo, useState } from "react";
import type { LiveValue, Point, Site } from "./api";
import { useAuth } from "./auth/AuthGate";
import { CommandPanel, OverrideBadge } from "./commands/CommandPanel";
import { STATUS_TEXT, formatTime, formatValue } from "./format";

interface Props {
  site: Site;
  points: Point[];
  live: Record<string, LiveValue>;
  onTrend: (id: string) => void;
}

/** All points with live values and quality (milestone 2 spec 8). */
export function PointsPage({ site, points, live, onTrend }: Props) {
  const [filter, setFilter] = useState("");
  const [commanding, setCommanding] = useState<string | null>(null);
  const { can, user } = useAuth();
  const canCommand = can("operator") && !!user.username;
  const target = points.find((p) => p.id === commanding) ?? null;

  const shown = useMemo(() => {
    const words = filter.toLowerCase().split(/\s+/).filter(Boolean);
    if (words.length === 0) return points;
    return points.filter((p) => {
      const haystack = [p.name, p.dis, p.type, ...Object.keys(p.tags)].join(" ").toLowerCase();
      return words.every((w) => haystack.includes(w));
    });
  }, [points, filter]);

  return (
    <section>
      <div className="toolbar">
        <label className="field">
          <span>Filter by name or tag</span>
          <input
            type="search"
            value={filter}
            placeholder="for example: elec power"
            onChange={(e) => setFilter(e.target.value)}
          />
        </label>
        <span className="muted">
          {shown.length} of {points.length} points
        </span>
      </div>
      <div className="table-wrap">
        <table className="points">
          <thead>
            <tr>
              <th>Point</th>
              <th className="wide-only">Equipment</th>
              <th className="num">Value</th>
              <th className="wide-only">Status</th>
              <th className="wide-only">Updated</th>
              <th className="wide-only" aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {shown.map((p) => {
              const cur = live[p.id] ?? p.cur;
              const good = cur.status === "ok";
              return (
                <tr key={p.id} className={good ? "" : "not-good"}>
                  <td>
                    <div>{p.dis}</div>
                    <div className="muted small mono">{p.name}</div>
                    <div className="tags">
                      {Object.keys(p.tags).map((t) => (
                        <span key={t} className="tag">
                          {t}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="wide-only">{p.equip?.dis ?? p.space?.dis ?? ""}</td>
                  <td className="num">
                    <div className="value" data-testid={`value-${p.name}`}>
                      {formatValue(p, cur)}
                    </div>
                    <OverrideBadge point={p} tz={site.ianaTz} />
                    <div className="narrow-only">
                      <span className={`status status-${cur.status}`}>{STATUS_TEXT[cur.status] ?? cur.status}</span>
                    </div>
                  </td>
                  <td className="wide-only">
                    <span className={`status status-${cur.status}`}>{STATUS_TEXT[cur.status] ?? cur.status}</span>
                    {!good && cur.reason && <div className="muted small">{cur.reason}</div>}
                  </td>
                  <td className="muted small wide-only">{formatTime(good ? cur.ts : cur.lastGoodTs, site.ianaTz)}</td>
                  <td className="wide-only">
                    {p.his && p.kind !== "Str" && (
                      <button type="button" className="link" onClick={() => onTrend(p.id)}>
                        Trend
                      </button>
                    )}
                    {canCommand && p.writable && (
                      <button type="button" className="link" onClick={() => setCommanding(p.id)}>
                        Command
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {target && (
        <div className="command-overlay" onClick={(e) => e.target === e.currentTarget && setCommanding(null)}>
          <CommandPanel point={target} live={live[target.id] ?? target.cur} tz={site.ianaTz} onClose={() => setCommanding(null)} />
        </div>
      )}
    </section>
  );
}
