// The HMI Schedules tab (milestone 7B): each schedule's state now and its next change. View only;
// schedules are changed in the web app.
import { useEffect, useState } from "react";
import type { Schedule } from "../schedules/schedulesApi";
import { listSchedules, stateText } from "../schedules/schedulesApi";

export function HmiSchedules() {
  const [list, setList] = useState<Schedule[] | null>(null);
  useEffect(() => {
    const load = () => listSchedules().then(setList).catch(() => undefined);
    load();
    const t = window.setInterval(load, 30000);
    return () => window.clearInterval(t);
  }, []);
  if (!list) return null;
  if (!list.length) return <p className="hmi-empty">No schedules on this site yet.</p>;
  return (
    <div className="hmi-setpoints">
      {list.map((s) => (
        <section key={s.name} className="hmi-card">
          <div className="hmi-sp-row">
            <div>
              <div>{s.dis}</div>
              <div className="hmi-sched-why">{s.enabled ? s.now?.why : "Not running"}</div>
            </div>
            <div className={`hmi-sp-value ${s.now?.occupied ? "occupied" : ""}`}>{s.enabled ? stateText(s) : "Off"}</div>
          </div>
        </section>
      ))}
    </div>
  );
}
