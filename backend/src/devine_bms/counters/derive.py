"""Counter points made and removed automatically (main spec 5.10, decided: made automatically).

Every enabled piece of equipment with a **run-state point** (a Bool sensor tagged `run`, on a real
device) gets two read-only Number points on the built-in `bms_counters` device:
`<room>_<equip>_run_hours` (unit h, tagged `devineRunHours`) and `<room>_<equip>_starts` (tagged
`devineStarts`). They are kept in the setup like other points (so they export, can carry alarms
and are logged for trends) and are removed when the run point goes. A point that a person edited
(display name, alarms, logging) keeps those edits: only missing points are added and orphans
removed. Works on the setup in its file form (camelCase), before the setup checks.
"""

from __future__ import annotations

import copy
from typing import Any

DEVICE = "bms_counters"
COUNTERS = {
    "run_hours": {"suffix": "run_hours", "dis": "run hours", "unit": "h", "tag": "devineRunHours"},
    "starts": {"suffix": "starts", "dis": "starts", "unit": None, "tag": "devineStarts"},
}


def run_point(data: dict, equip: str) -> dict | None:
    """The equipment's run-state point: a Bool sensor tagged `run` on a real device (first by name)."""
    devices = {d["name"]: d for d in data.get("devices") or []}
    found = [p for p in data.get("points") or []
             if p.get("equip") == equip and p.get("kind") == "Bool" and p.get("type") == "sensor"
             and (p.get("tags") or {}).get("run") is True
             and devices.get(p.get("device"), {}).get("protocol") not in ("counter", None)]
    return min(found, key=lambda p: p["name"]) if found else None


def device_hours_point(data: dict, equip: str) -> dict | None:
    """A run-hours point the device itself reports (tagged `devineRunHours`, not one of ours)."""
    found = [p for p in data.get("points") or []
             if p.get("equip") == equip and p.get("kind") == "Number"
             and (p.get("tags") or {}).get("devineRunHours") is True and p.get("device") != DEVICE]
    return min(found, key=lambda p: p["name"]) if found else None


def derive(data: dict) -> tuple[dict, list[str]]:
    """The setup with its counter points brought up to date, and what changed (for the audit log)."""
    data = copy.deepcopy(data)
    points: list[dict] = data.setdefault("points", [])
    devices: list[dict] = data.setdefault("devices", [])
    equips = {e["name"]: e for e in data.get("equips") or []}
    taken = {p["name"] for p in points}
    changes: list[str] = []

    wanted: dict[tuple[str, str], dict] = {}
    for name, e in equips.items():
        if run_point(data, name) is None:
            continue
        room = e.get("space") or "site"
        for counter, spec in COUNTERS.items():
            point: dict[str, Any] = {
                "name": f"{room}_{name}_{spec['suffix']}", "dis": f"{e.get('dis') or name} {spec['dis']}",
                "equip": name, "device": DEVICE, "type": "sensor", "kind": "Number",
                "tags": {spec["tag"]: True}, "address": {"counter": counter, "equip": name},
                "pollClass": "slow", "his": True,
            }
            if spec["unit"]:
                point["unit"] = spec["unit"]
            wanted[(name, counter)] = point

    ours = [p for p in points if p.get("device") == DEVICE]
    have = {((p.get("address") or {}).get("equip"), (p.get("address") or {}).get("counter")): p for p in ours}
    # Remove counters whose equipment or run point is gone.
    for key, p in have.items():
        if key not in wanted:
            points.remove(p)
            changes.append(f"removed the counter point {p['name']}")
    # Add the missing ones (a name already used by another point is left alone).
    for key, p in wanted.items():
        if key in have:
            continue
        if p["name"] in taken:
            changes.append(f"could not add {p['name']}: another point has that name")
            continue
        points.append(p)
        taken.add(p["name"])
        changes.append(f"added the counter point {p['name']}")

    has_points = any(p.get("device") == DEVICE for p in points)
    device = next((d for d in devices if d["name"] == DEVICE), None)
    if has_points and device is None:
        devices.append({"name": DEVICE, "dis": "BMS counters", "protocol": "counter", "connection": {}})
    elif not has_points and device is not None and device.get("protocol") == "counter":
        devices.remove(device)
    return data, changes
