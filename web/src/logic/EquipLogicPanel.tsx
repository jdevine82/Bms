// The equipment panel's logic (main spec 5.3, milestone 10C): the diagrams that write this
// equipment's points, and whether it is BMS-controlled or device-controlled.
import { useEffect, useState } from "react";
import { get } from "../setup/api";

interface ByEquip {
  equip: string;
  dis: string;
  control: "bms" | "device";
  diagrams: { name: string; state: string; points: string[]; template: boolean }[];
}

export function EquipLogicPanel({ equip }: { equip: string }) {
  const [info, setInfo] = useState<ByEquip | null>(null);
  useEffect(() => {
    get<ByEquip[]>("/api/logic/by-equip").then((all) => setInfo(all.find((e) => e.equip === equip) ?? null)).catch(() => undefined);
  }, [equip]);
  if (!info) return null;
  return (
    <div className="block" aria-label="Logic">
      <h3>Logic</h3>
      <p>
        <strong>{info.control === "bms" ? "BMS-controlled" : "Device-controlled"}</strong>{" "}
        <span className="muted small">{info.control === "bms" ? "a running logic diagram writes its points" : "no running logic writes its points"}</span>
      </p>
      {info.diagrams.length > 0 && (
        <ul>
          {info.diagrams.map((d) => (
            <li key={d.name}>
              <a href={`#/logic/${encodeURIComponent(d.name)}`}>{d.name}</a>{d.template ? " (template logic)" : ""}: {d.state}
              <span className="muted small"> · writes {d.points.join(", ")}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
