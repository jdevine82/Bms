"""Maintenance counters (main spec 5.10): run hours and starts of equipment, counted by the BMS.

- For each piece of equipment with a run-state point (counters/derive.py), every second: while the
  run state is Good and was on, the time counts as run time; an off-to-on change is a start.
  Time when the state is Bad or Stale is not counted: it is kept as "unseen" and the counters are
  shown as estimated (5.2.1).
- A device that reports its own run hours (or starts) — a point on the equipment tagged
  `devineRunHours` (or `devineStarts`) — is shown and used instead, so a BMS outage leaves no gap.
- The counter points (`<room>_<equip>_run_hours`, `_starts`) get the values, so trends, alarms and
  logic can use them.
- **Service reminders:** an interval in hours or starts (the equipment's, else its template's).
  When the count since the last service reaches it, a system alarm is raised ("Service due: …").
  An admin marks the service done: the "since service" counts start again; totals never reset.
- Saved to the database every minute and on stop. On a standalone Pi the counting columns are
  "quiet", so the card is written daily and when a service or reminder happens (standalone/state).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from devine_bms.config.model import CounterAddress
from devine_bms.db import models as db
from devine_bms.values import Status

log = logging.getLogger(__name__)
PUSH_EVERY_S = 10.0


@dataclass
class Counted:
    equip_id: uuid.UUID
    name: str
    dis: str
    run_point: Any
    device_hours: Any = None  # a point the device reports its own run hours on
    device_starts: Any = None
    points: dict[str, Any] = field(default_factory=dict)  # counter -> our counter point
    service: dict | None = None  # {hours, starts, level, message}
    row: Any = None  # db.EquipCounter (detached copy, saved every minute)
    seen_on: bool = False  # the run state was Good and on at the last tick


class CounterEngine:
    def __init__(self, runtime, sessions, site_name: str, alarms=None, record=None, tick_s: float = 1.0,
                 save_every_s: float = 60.0, clock=time.monotonic) -> None:
        self.runtime = runtime
        self.sessions = sessions
        self.site_name = site_name
        self.alarms = alarms
        self.record = record
        self.tick_s = tick_s
        self.save_every_s = save_every_s
        self.clock = clock
        self.equips: dict[str, Counted] = {}
        self._pushed: dict[uuid.UUID, tuple[Any, float]] = {}
        self._last_tick: float | None = None
        self._last_save = clock()
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    # ---------- life ----------

    async def start(self) -> None:
        await self.reload()
        self._task = asyncio.create_task(self._loop(), name="counters")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        await self.save()

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("counter tick failed")
            await asyncio.sleep(self.tick_s)

    async def reload(self) -> None:
        """Which equipment is counted, from the setup (after any setup change)."""
        async with self._lock:
            if self.equips:
                await self._save_rows()
            cfg = self.runtime.config
            devices = {d.id: d for d in cfg.devices}
            async with self.sessions() as session:
                site = await session.scalar(select(db.Site).where(db.Site.name == self.site_name))
                equips = (await session.scalars(select(db.Equip).where(db.Equip.site_id == site.id,
                                                                       db.Equip.enabled.is_(True)))).all()
                rows = {r.equip_id: r for r in (await session.scalars(select(db.EquipCounter))).all()}
                templates = {(t.get("id"), t.get("version")): t
                             for t in (site.settings or {}).get("equipTemplates") or []}
            from devine_bms.templates.equip import builtin_equip_templates

            for t in builtin_equip_templates().values():
                templates.setdefault((t.id, t.version), t.model_dump(mode="json", by_alias=True))
            by_equip: dict[uuid.UUID, list] = {}
            for p in cfg.points:
                if p.equip_id is not None:
                    by_equip.setdefault(p.equip_id, []).append(p)
            counted: dict[str, Counted] = {}
            for e in equips:
                pts = sorted(by_equip.get(e.id, []), key=lambda p: p.name)

                def real(p) -> bool:
                    d = devices.get(p.device_id)
                    return d is not None and d.protocol != "counter"

                run = next((p for p in pts if real(p) and p.kind.value == "Bool" and p.point_type == "sensor"
                            and p.tags.get("run") is True), None)
                if run is None:
                    continue
                c = Counted(e.id, e.name, e.dis, run)
                c.device_hours = next((p for p in pts if real(p) and p.kind.value == "Number"
                                       and p.tags.get("devineRunHours") is True), None)
                c.device_starts = next((p for p in pts if real(p) and p.kind.value == "Number"
                                        and p.tags.get("devineStarts") is True), None)
                for p in pts:
                    if isinstance(p.address, CounterAddress) and p.address.equip == e.name:
                        c.points[p.address.counter] = p
                link = e.template or {}
                c.service = e.service or (templates.get((link.get("id"), link.get("version"))) or {}).get("service")
                old = self.equips.get(e.name)
                c.seen_on = old.seen_on if old else False
                c.row = rows.get(e.id) or db.EquipCounter(equip_id=e.id, run_s=0.0, starts=0, unseen_s=0.0,
                                                           service_hours_mark=0.0, service_starts_mark=0)
                counted[e.name] = c
            # Equipment no longer counted: its reminder goes.
            for name in set(self.equips) - set(counted):
                self._alarm(self.equips[name], False)
            self.equips = counted
            self._last_tick = None

    # ---------- counting ----------

    async def tick(self) -> None:
        async with self._lock:
            now = self.clock()
            dt = 0.0 if self._last_tick is None else max(0.0, min(now - self._last_tick, 60.0))
            self._last_tick = now
            cache = self.runtime.cache
            for c in self.equips.values():
                row = c.row
                live = cache.get(c.run_point.id)
                if live is not None and live.status == Status.OK and live.value is not None:
                    state = bool(live.value)
                    if c.seen_on:
                        row.run_s += dt
                    if state and row.last_state is False:
                        row.starts += 1
                    row.last_state = state
                    c.seen_on = state
                else:
                    if live is not None and live.status != Status.UNKNOWN:
                        row.unseen_s += dt  # Bad or Stale: not counted, and the counters are estimated (5.10)
                    c.seen_on = False
                self._push(c)
                self._check_service(c)
            if now - self._last_save >= self.save_every_s:
                await self._save_rows()

    def totals(self, c: Counted) -> dict[str, Any]:
        """Run hours and starts as shown: the device's own where it reports them, else the BMS's."""
        cache = self.runtime.cache

        def device(p) -> float | None:
            v = cache.get(p.id) if p is not None else None
            return float(v.value) if v is not None and v.status == Status.OK and v.value is not None else None

        dh, ds = device(c.device_hours), device(c.device_starts)
        return {"hours": dh if dh is not None else c.row.run_s / 3600.0,
                "starts": int(ds) if ds is not None else int(c.row.starts),
                "hoursSource": "device" if dh is not None else "bms",
                "startsSource": "device" if ds is not None else "bms"}

    def _push(self, c: Counted) -> None:
        tracker = getattr(self.runtime, "tracker", None)
        if tracker is None:
            return
        t = self.totals(c)
        mono = self.clock()
        for counter, value in (("run_hours", round(t["hours"], 3)), ("starts", t["starts"])):
            p = c.points.get(counter)
            if p is None:
                continue
            last = self._pushed.get(p.id)
            if last is not None and last[0] == value and mono - last[1] < PUSH_EVERY_S:
                continue
            self._pushed[p.id] = (value, mono)
            tracker.good(p, float(value))

    # ---------- service reminders ----------

    def since_service(self, c: Counted) -> dict[str, float]:
        t = self.totals(c)
        return {"hours": max(0.0, t["hours"] - c.row.service_hours_mark),
                "starts": max(0, t["starts"] - int(c.row.service_starts_mark))}

    def _due(self, c: Counted) -> str | None:
        s = c.service or {}
        since = self.since_service(c)
        if s.get("hours") and since["hours"] >= float(s["hours"]):
            return f"{s['hours']:,.0f} hours"
        if s.get("starts") and since["starts"] >= int(s["starts"]):
            return f"{int(s['starts']):,} starts"
        return None

    def _check_service(self, c: Counted) -> None:
        due = self._due(c)
        if due and c.row.due_at is None:
            c.row.due_at = datetime.now(UTC)  # a change worth saving at once on a standalone Pi
        elif not due and c.row.due_at is not None:
            c.row.due_at = None
        self._alarm(c, bool(due), due)

    def _alarm(self, c: Counted, active: bool, due: str | None = None) -> None:
        if self.alarms is None:
            return
        s = c.service or {}
        message = (s.get("message") or f"Service due: {c.name}, {due}") if active else ""
        self.alarms.set_system(f"service:{c.name}", active, str(s.get("level") or "info"), message,
                               c.name, c.dis)

    async def service_done(self, equip: str, actor) -> dict:
        async with self._lock:
            c = self.equips.get(equip)
            if c is None:
                raise LookupError(f"'{equip}' has no counters (no run-state point)")
            t = self.totals(c)
            before = self.since_service(c)
            c.row.service_hours_mark, c.row.service_starts_mark = t["hours"], t["starts"]
            c.row.serviced_at = datetime.now(UTC)
            c.row.serviced_by = (getattr(actor, "username", None) or "")[:64] or None
            c.row.due_at = None
            self._alarm(c, False)
            await self._save_rows()
        if self.record is not None:
            await self.record(actor, "service-done", f"marked the service of {equip} done after "
                              f"{before['hours']:,.1f} hours and {before['starts']:,} starts since the last one",
                              kind="equip", name=equip, before=before, after={"hours": 0, "starts": 0})
        return self.status_of(c)

    # ---------- saving and reading ----------

    async def _save_rows(self) -> None:
        self._last_save = self.clock()
        if not self.equips:
            return
        async with self.sessions() as session, session.begin():
            for c in self.equips.values():
                c.row = await session.merge(c.row)
            await session.flush()
            for c in self.equips.values():
                session.expunge(c.row)

    async def save(self) -> None:
        async with self._lock:
            await self._save_rows()

    def status_of(self, c: Counted) -> dict[str, Any]:
        t = self.totals(c)
        since = self.since_service(c)
        s = c.service or {}
        return {"equip": c.name, "dis": c.dis, "runPoint": c.run_point.name,
                "running": bool(c.seen_on), "hours": round(t["hours"], 2), "starts": t["starts"],
                "hoursSource": t["hoursSource"], "startsSource": t["startsSource"],
                "estimated": c.row.unseen_s > 0, "unseenHours": round(c.row.unseen_s / 3600.0, 2),
                "sinceService": {"hours": round(since["hours"], 2), "starts": since["starts"]},
                "service": {k: s.get(k) for k in ("hours", "starts", "level", "message") if s.get(k) is not None}
                or None,
                "due": bool(c.row.due_at), "dueAt": c.row.due_at.isoformat() if c.row.due_at else None,
                "servicedAt": c.row.serviced_at.isoformat() if c.row.serviced_at else None,
                "servicedBy": c.row.serviced_by,
                "points": {k: p.name for k, p in c.points.items()}}

    def status(self) -> list[dict[str, Any]]:
        return [self.status_of(c) for c in sorted(self.equips.values(), key=lambda c: c.name)]
