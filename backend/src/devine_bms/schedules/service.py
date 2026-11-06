"""Running occupancy schedules (main spec 5.4; milestone 7B).

Every `tick_s` the service works out each enabled schedule's state in the site's time zone.
When a point's wanted value changes (and at start-up) it writes it: on BACnet at priority 16,
so an operator's hand mode at priority 8 still wins; on Modbus and MQTT a point in hand mode
is left alone and gets the schedule's value when hand mode ends (CommandService asks
`value_for`). State changes and every edit are in the audit log.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import select

from devine_bms.auth.service import Actor
from devine_bms.commands.writer import SCHEDULE_PRIORITY, WriteError, write_point
from devine_bms.config.model import BacnetAddress
from devine_bms.db import models as db
from devine_bms.schedules import bacnet as sched_bacnet
from devine_bms.schedules.model import CalendarIn, ScheduleIn, next_change, state_at

log = logging.getLogger(__name__)
RUNNER = Actor(client="server", role="admin", username="schedule")
READ_ONLY = {"now", "next", "equipsResolved", "points", "controllerState"}


class ScheduleError(Exception):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.message, self.status = message, status

    def as_dict(self) -> dict:
        return {"ok": False, "error": self.message}


def _check(model, data: dict):
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(x) for x in first["loc"])
        raise ScheduleError(f"{where}: {first['msg']}" if where else first["msg"]) from None


class ScheduleService:
    def __init__(self, runtime, sessions, site_name: str, tz: str, record=None, overrides=None,
                 tick_s: float = 15.0) -> None:
        self.runtime = runtime
        self.sessions = sessions
        self.site_name = site_name
        self.tz = ZoneInfo(tz)
        self.record = record
        self.overrides = overrides or (lambda: {})  # point id -> hand-mode override (CommandService)
        self.tick_s = tick_s
        self.schedules: dict[str, ScheduleIn] = {}
        self.calendars: dict[str, CalendarIn] = {}
        self.controller_state: dict[str, dict | None] = {}
        self.applied: dict[uuid.UUID, Any] = {}  # point id -> value last written by a schedule
        self.state: dict[str, bool] = {}
        self.lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._site_id = None

    # ---------- life ----------

    async def load(self) -> None:
        async with self.sessions() as session:
            site = await session.scalar(select(db.Site).where(db.Site.name == self.site_name))
            self._site_id = site.id
            cals = (await session.scalars(select(db.HolidayCalendar).where(db.HolidayCalendar.site_id == site.id))).all()
            rows = (await session.scalars(select(db.Schedule).where(db.Schedule.site_id == site.id))).all()
        self.calendars = {c.name: CalendarIn(name=c.name, dis=c.dis, dates=c.dates) for c in cals}
        self.schedules = {r.name: ScheduleIn.model_validate({
            "name": r.name, "dis": r.dis, "enabled": r.enabled, "weekly": r.weekly, "calendars": r.calendars,
            "exceptions": r.exceptions, "equips": r.equips, "tags": r.tags, "controller": r.controller}) for r in rows}
        self.controller_state = {r.name: r.controller_state for r in rows}

    async def start(self) -> None:
        await self.load()
        self._task = asyncio.create_task(self._loop(), name="schedules")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                log.exception("schedule pass failed")
            await asyncio.sleep(self.tick_s)

    def now_local(self) -> datetime:
        return datetime.now(UTC).astimezone(self.tz).replace(tzinfo=None)

    # ---------- which equipment and points ----------

    def equips_of(self, s: ScheduleIn) -> set[str]:
        names = set(s.equips)
        for e in self.runtime.config.equips:
            if any(t in (e.tags or {}) for t in s.tags):
                names.add(e.name)
        return names

    def points_of(self, s: ScheduleIn) -> list:
        equips = {e.id for e in self.runtime.config.equips if e.name in self.equips_of(s)}
        return [p for p in self.runtime.config.points if p.equip_id in equips and p.occupancy and p.writable]

    def schedule_for(self, point) -> ScheduleIn | None:
        for s in self.schedules.values():
            if s.enabled and point in self.points_of(s):
                return s
        return None

    def value_for(self, point) -> Any:
        """The schedule's value for a point now, or None (the hook CommandService uses)."""
        s = self.schedule_for(point)
        if s is None:
            return None
        occupied, _ = state_at(s, self.calendars, self.now_local())
        return point.occupancy["occupied" if occupied else "unoccupied"]

    # ---------- running ----------

    async def run_once(self) -> None:
        async with self.lock:
            now = self.now_local()
            for s in list(self.schedules.values()):
                if not s.enabled:
                    continue
                occupied, why = state_at(s, self.calendars, now)
                written, skipped, failed = [], [], []
                for p in self.points_of(s):
                    want = p.occupancy["occupied" if occupied else "unoccupied"]
                    if self.applied.get(p.id) == want:
                        continue
                    if p.id in self.overrides() and not isinstance(p.address, BacnetAddress):
                        skipped.append(p.name)  # hand mode: it gets this value when hand mode ends
                        continue
                    try:
                        await write_point(self.runtime, p, want, SCHEDULE_PRIORITY)
                        self.applied[p.id] = want
                        written.append(p.name)
                    except WriteError as exc:
                        failed.append(f"{p.name} ({exc})")  # tried again next pass
                changed = self.state.get(s.name) != occupied
                self.state[s.name] = occupied
                if changed or written or failed:
                    state = "Occupied" if occupied else "Unoccupied"
                    summary = f"schedule {s.name}: {state} ({why})"
                    if written:
                        summary += f"; wrote {len(written)} point{'s' if len(written) > 1 else ''}"
                    if skipped:
                        summary += f"; {len(skipped)} in hand mode"
                    if failed:
                        summary += f"; {len(failed)} failed"
                    await self._audit(RUNNER, "schedule-run", summary, s.name, None,
                                      {"state": state, "written": written, "handMode": skipped, "failed": failed})

    # ---------- reading ----------

    def describe(self, s: ScheduleIn) -> dict:
        now = self.now_local()
        occupied, why = state_at(s, self.calendars, now)
        nxt = next_change(s, self.calendars, now)
        return {**s.model_dump(mode="json", by_alias=True),
                "now": {"occupied": occupied, "why": why},
                "next": {"at": nxt[0].isoformat(timespec="minutes"), "occupied": nxt[1]} if nxt else None,
                "equipsResolved": sorted(self.equips_of(s)),
                "points": [{"name": p.name, "dis": p.dis, "occupancy": p.occupancy} for p in self.points_of(s)],
                "controllerState": self.controller_state.get(s.name)}

    # ---------- changes ----------

    async def save(self, actor: Actor, data: dict, name: str | None = None) -> dict:
        # A schedule sent back as it was read carries fields the BMS works out; they are ignored.
        data = {k: v for k, v in data.items() if k not in READ_ONLY}
        s = _check(ScheduleIn, data)
        if name is not None and s.name != name:
            raise ScheduleError("the name in the body differs from the address")
        unknown = [c for c in s.calendars if c not in self.calendars]
        if unknown:
            raise ScheduleError(f"no holiday calendar {unknown}")
        equip_names = {e.name for e in self.runtime.config.equips}
        missing = [e for e in s.equips if e not in equip_names]
        if missing:
            raise ScheduleError(f"no equipment {missing}")
        mine = self.equips_of(s)
        for other in self.schedules.values():
            if other.name != s.name and other.enabled and s.enabled:
                both = mine & self.equips_of(other)
                if both:
                    raise ScheduleError(f"{', '.join(sorted(both))} already follow{'s' if len(both) == 1 else ''} "
                                        f"schedule {other.name}; equipment follows one schedule")
        if s.controller is not None:
            device = next((d for d in self.runtime.config.devices if d.name == s.controller.device), None)
            if device is None or device.protocol != "bacnet_ip":
                raise ScheduleError("a controller copy needs a BACnet/IP device (MS/TP comes after the bench check)")
        before = self.schedules.get(s.name)
        async with self.lock, self.sessions() as session, session.begin():
            row = await session.scalar(select(db.Schedule).where(db.Schedule.site_id == self._site_id,
                                                                 db.Schedule.name == s.name))
            if row is None:
                row = db.Schedule(site_id=self._site_id, name=s.name)
                session.add(row)
            dumped = s.model_dump(mode="json", by_alias=True)
            row.dis, row.enabled, row.weekly, row.calendars = s.dis, s.enabled, dumped["weekly"], s.calendars
            row.exceptions, row.equips, row.tags, row.controller = dumped["exceptions"], s.equips, s.tags, dumped["controller"]
            self.schedules[s.name] = s
        # Points of this schedule are written again by the next pass.
        for p in self.points_of(s):
            self.applied.pop(p.id, None)
        await self._audit(actor, "schedule", f"{'changed' if before else 'added'} schedule {s.name}", s.name,
                          before.model_dump(mode="json", by_alias=True) if before else None,
                          s.model_dump(mode="json", by_alias=True))
        await self.run_once()
        return self.describe(s)

    async def remove(self, actor: Actor, name: str) -> None:
        s = self.schedules.get(name)
        if s is None:
            raise ScheduleError(f"no schedule '{name}'", 404)
        async with self.lock, self.sessions() as session, session.begin():
            row = await session.scalar(select(db.Schedule).where(db.Schedule.site_id == self._site_id,
                                                                 db.Schedule.name == name))
            await session.delete(row)
            self.schedules.pop(name)
            self.state.pop(name, None)
        await self._audit(actor, "schedule", f"removed schedule {name}", name,
                          s.model_dump(mode="json", by_alias=True), None)

    async def save_calendar(self, actor: Actor, data: dict, name: str | None = None) -> dict:
        c = _check(CalendarIn, data)
        if name is not None and c.name != name:
            raise ScheduleError("the name in the body differs from the address")
        before = self.calendars.get(c.name)
        async with self.lock, self.sessions() as session, session.begin():
            row = await session.scalar(select(db.HolidayCalendar).where(db.HolidayCalendar.site_id == self._site_id,
                                                                        db.HolidayCalendar.name == c.name))
            if row is None:
                row = db.HolidayCalendar(site_id=self._site_id, name=c.name)
                session.add(row)
            row.dis, row.dates = c.dis, c.model_dump(mode="json", by_alias=True)["dates"]
            self.calendars[c.name] = c
        self.applied.clear()
        await self._audit(actor, "schedule", f"{'changed' if before else 'added'} holiday calendar {c.name}", c.name,
                          before.model_dump(mode="json") if before else None, c.model_dump(mode="json"))
        await self.run_once()
        return c.model_dump(mode="json", by_alias=True)

    async def remove_calendar(self, actor: Actor, name: str) -> None:
        users = [s.name for s in self.schedules.values() if name in s.calendars]
        if users:
            raise ScheduleError(f"holiday calendar {name} is used by {users}")
        if name not in self.calendars:
            raise ScheduleError(f"no holiday calendar '{name}'", 404)
        async with self.lock, self.sessions() as session, session.begin():
            row = await session.scalar(select(db.HolidayCalendar).where(db.HolidayCalendar.site_id == self._site_id,
                                                                        db.HolidayCalendar.name == name))
            await session.delete(row)
            self.calendars.pop(name)
        await self._audit(actor, "schedule", f"removed holiday calendar {name}", name, None, None)

    # ---------- the copy in a controller ----------

    def _target(self, name: str):
        s = self.schedules.get(name)
        if s is None:
            raise ScheduleError(f"no schedule '{name}'", 404)
        if s.controller is None:
            raise ScheduleError("this schedule has no controller copy")
        if self.runtime.bacnet is None:
            raise ScheduleError("the BACnet link is not running")
        device = next(d for d in self.runtime.config.devices if d.name == s.controller.device)
        from bacpypes3.primitivedata import ObjectIdentifier

        from devine_bms.drivers.bacnet.link import device_address
        return s, device_address(device.connection.address), ObjectIdentifier(f"schedule,{s.controller.instance}")

    async def push(self, actor: Actor, name: str) -> dict:
        s, address, oid = self._target(name)
        want = sched_bacnet.plan(s, self.calendars, s.controller, self.now_local().date())
        weekly, exceptions, default = sched_bacnet.to_bacnet(want, s.controller)
        app = self.runtime.bacnet.app
        try:
            for prop, value in (("scheduleDefault", default), ("weeklySchedule", weekly),
                                ("exceptionSchedule", exceptions)):
                await asyncio.wait_for(app.write_property(address, oid, prop, value), 10)
        except Exception as exc:
            raise ScheduleError(f"writing the controller's schedule failed: {exc}", 502) from exc
        state = {"pushedAt": datetime.now(UTC).isoformat(), "pushedBy": actor.username, "inSync": True,
                 "checkedAt": datetime.now(UTC).isoformat(), "differences": []}
        await self._save_state(name, state)
        await self._audit(actor, "schedule-push", f"wrote schedule {name} into {s.controller.device} "
                          f"schedule {s.controller.instance}", name, None, want)
        return state

    async def check(self, name: str) -> dict:
        """Read the controller's copy and compare it with what the BMS would write now."""
        s, address, oid = self._target(name)
        app = self.runtime.bacnet.app
        try:
            weekly = await asyncio.wait_for(app.read_property(address, oid, "weeklySchedule"), 10)
            exceptions = await asyncio.wait_for(app.read_property(address, oid, "exceptionSchedule"), 10)
        except Exception as exc:
            raise ScheduleError(f"reading the controller's schedule failed: {exc}", 502) from exc
        have = sched_bacnet.from_bacnet(weekly, exceptions, s.controller)
        want = sched_bacnet.plan(s, self.calendars, s.controller, self.now_local().date())
        diffs = sched_bacnet.differences(want, have)
        state = {**(self.controller_state.get(name) or {}), "checkedAt": datetime.now(UTC).isoformat(),
                 "inSync": not diffs, "differences": diffs, "controllerWeekly": have["weekly"]}
        await self._save_state(name, state)
        return state

    async def accept(self, actor: Actor, name: str) -> dict:
        """Take the controller's weekly pattern into the BMS schedule (its exceptions are not taken)."""
        s = self.schedules.get(name)
        state = await self.check(name)
        occ = sched_bacnet._value(s.controller, True)
        weekly: dict[str, list[dict]] = {}
        from devine_bms.schedules.model import DAYS
        for i, day in enumerate(DAYS):
            entries = state["controllerWeekly"][i] if i < len(state["controllerWeekly"]) else []
            periods, start = [], None
            for t, v in entries:
                if v == occ and start is None:
                    start = t
                elif v != occ and start is not None:
                    periods.append({"start": start, "end": t})
                    start = None
            if start is not None:
                periods.append({"start": start, "end": "24:00"})
            if periods:
                weekly[day] = periods
        data = s.model_dump(mode="json", by_alias=True) | {"weekly": weekly}
        result = await self.save(actor, data, name)
        await self._audit(actor, "schedule", f"took the weekly pattern of schedule {name} from its controller",
                          name, None, weekly)
        return result

    async def _save_state(self, name: str, state: dict) -> None:
        self.controller_state[name] = state
        async with self.sessions() as session, session.begin():
            row = await session.scalar(select(db.Schedule).where(db.Schedule.site_id == self._site_id,
                                                                 db.Schedule.name == name))
            row.controller_state = state

    async def _audit(self, actor: Actor, action: str, summary: str, name: str | None, before: Any, after: Any):
        if self.record is not None:
            await self.record(actor, action, summary, kind="schedule", name=name, before=before, after=after)
