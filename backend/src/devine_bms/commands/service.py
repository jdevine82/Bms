"""Commands and hand-mode overrides (main spec 5.6; milestone 7A).

- Only setpoints and commands whose address can be written are commanded, by operators and
  admins. Number points need write limits, enforced here for every role.
- Start/stop (Bool) commands and every override need `confirm` (the screens ask first).
- **Set** is a lasting change. **Hand** is an override for a chosen time (the site's choices,
  default 1, 2 or 8 hours, 2 by default); when it ends the point returns to auto: on BACnet the
  priority-8 value is released; elsewhere the schedule's value (milestone 7B) or the value from
  before the override is written back (milestone 7 decision 3). Overrides are stored, so they
  survive a restart.
- Every command, release and override end is in the audit log, with the old and new value.
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select

from devine_bms.auth.service import Actor
from devine_bms.commands.writer import MANUAL_PRIORITY, WriteError, write_point
from devine_bms.config.model import BacnetAddress, Kind
from devine_bms.db import models as db
from devine_bms.runtime_model import RuntimePoint
from devine_bms.values import Status

log = logging.getLogger(__name__)

DEFAULT_SETTINGS = {"choicesH": [1, 2, 8], "defaultH": 2}
TIMER = Actor(client="server", role="admin", username="override timer")


class CommandError(Exception):
    def __init__(self, message: str, status: int = 422, code: str | None = None) -> None:
        super().__init__(message)
        self.message, self.status, self.code = message, status, code

    def as_dict(self) -> dict:
        return {"ok": False, "error": self.message, **({"code": self.code} if self.code else {})}


def parse_value(point: RuntimePoint, raw: Any) -> float | bool | str:
    if point.kind == Kind.BOOL:
        if isinstance(raw, bool):
            return raw
        if str(raw).lower() in ("true", "1", "on"):
            return True
        if str(raw).lower() in ("false", "0", "off"):
            return False
        raise CommandError("give true or false")
    if point.kind == Kind.STR:
        match = next((e for e in point.enum or [] if e.lower() == str(raw).lower()), None)
        if match is None:
            raise CommandError(f"give one of {list(point.enum or [])}")
        return match
    if isinstance(raw, bool):
        raise CommandError("give a number")
    try:
        number = float(raw)
    except (TypeError, ValueError):
        raise CommandError("give a number") from None
    if not math.isfinite(number):
        raise CommandError("give a number")
    limits = point.write_limits
    if limits is None:
        raise CommandError("this point has no write limits yet; an admin sets them in its setup (main spec 5.6)",
                           code="no-limits")
    if not limits.min <= number <= limits.max:
        raise CommandError(f"{number:g} is outside the write limits {limits.min:g} to {limits.max:g}",
                           code="limits")
    return number


def override_json(row: db.PointOverride) -> dict:
    return {"value": row.value.get("v"), "setBy": row.set_by, "setAt": row.set_at.isoformat(),
            "endsAt": row.ends_at.isoformat()}


class CommandService:
    def __init__(self, runtime, sessions, site_name: str, record: Callable | None = None,
                 schedule_value: Callable[[RuntimePoint], Any] | None = None, check_s: float = 5.0) -> None:
        self.runtime = runtime
        self.sessions = sessions
        self.site_name = site_name
        self.record = record  # AuthService.record, for the audit log
        self.schedule_value = schedule_value or (lambda point: None)  # milestone 7B
        self.check_s = check_s
        self.overrides: dict[uuid.UUID, dict] = {}
        self.subscribers: set[asyncio.Queue] = set()
        self._locks: dict[uuid.UUID, asyncio.Lock] = {}
        self._task: asyncio.Task | None = None

    # ---------- life ----------

    async def start(self) -> None:
        async with self.sessions() as session:
            rows = (await session.scalars(select(db.PointOverride))).all()
        self.overrides = {r.point_id: override_json(r) for r in rows}
        self._task = asyncio.create_task(self._expiry_loop(), name="override timers")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    def _publish(self, point_id: uuid.UUID) -> None:
        message = {"pointId": str(point_id), "override": self.overrides.get(point_id)}
        for q in list(self.subscribers):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                pass

    # ---------- settings ----------

    async def settings(self) -> dict:
        async with self.sessions() as session:
            site = await session.scalar(select(db.Site).where(db.Site.name == self.site_name))
            return {**DEFAULT_SETTINGS, **(site.command_settings or {})}

    async def save_settings(self, actor: Actor, body: dict) -> dict:
        choices = body.get("choicesH", DEFAULT_SETTINGS["choicesH"])
        default = body.get("defaultH", DEFAULT_SETTINGS["defaultH"])
        if not isinstance(choices, list) or not choices or len(choices) > 6 or \
                any(not isinstance(c, (int, float)) or not 0.25 <= c <= 24 for c in choices):
            raise CommandError("give 1 to 6 override times, each 0.25 to 24 hours")
        if default not in choices:
            raise CommandError("the default override time must be one of the choices")
        settings = {"choicesH": sorted(set(choices)), "defaultH": default}
        async with self.sessions() as session, session.begin():
            site = await session.scalar(select(db.Site).where(db.Site.name == self.site_name))
            site.command_settings = settings
        await self._audit(actor, "settings", "changed the override times", None, None, settings)
        return settings

    # ---------- commands ----------

    def point(self, point_id: uuid.UUID) -> RuntimePoint:
        point = next((p for p in self.runtime.config.points if p.id == point_id), None)
        if point is None:
            raise CommandError("point not found", 404)
        return point

    def _current(self, point: RuntimePoint) -> Any:
        live = self.runtime.cache.get(point.id)
        return live.value if live is not None and live.status in (Status.OK, Status.STALE) else None

    async def command(self, actor: Actor, point_id: uuid.UUID, raw: Any, mode: str = "set",
                      hours: float | None = None, confirm: bool = False) -> dict:
        point = self.point(point_id)
        if not point.writable:
            raise CommandError("this point cannot be commanded (only writable setpoints and commands)")
        if mode not in ("set", "hand"):
            raise CommandError("mode is set or hand")
        value = parse_value(point, raw)
        if (point.kind == Kind.BOOL or mode == "hand") and not confirm:
            raise CommandError("confirm this command first", 409, "confirm")
        settings = await self.settings()
        if mode == "hand":
            hours = settings["defaultH"] if hours is None else hours
            if hours not in settings["choicesH"]:
                raise CommandError(f"the override time is one of {settings['choicesH']} hours")
        lock = self._locks.setdefault(point.id, asyncio.Lock())
        async with lock:
            before = self._current(point)
            if mode == "hand" and point.id not in self.overrides:
                # The value to return to: read it from the device now, as the last poll can be
                # seconds old (a value set just before would otherwise be lost).
                fresh = await self._read_back(point)
                if fresh and fresh["status"] == "ok":
                    before = fresh["value"]
            try:
                await write_point(self.runtime, point, value, MANUAL_PRIORITY)
            except WriteError as exc:
                await self._audit(actor, "command-failed", f"command to {point.name} failed: {exc}", point,
                                  {"value": before}, {"value": value, "mode": mode})
                raise CommandError(f"the write failed: {exc}", 502) from exc
            now = datetime.now(UTC)
            async with self.sessions() as session, session.begin():
                existing = await session.get(db.PointOverride, point.id)
                if mode == "hand":
                    previous = existing.previous if existing else {"v": before}
                    row = existing or db.PointOverride(point_id=point.id, previous=previous)
                    row.value, row.set_by, row.set_at = {"v": value}, actor.username or actor.label, now
                    row.ends_at = now + timedelta(hours=hours)
                    session.add(row)
                    await session.flush()
                    self.overrides[point.id] = override_json(row)
                elif existing is not None:  # a lasting change ends hand mode
                    await session.delete(existing)
                    self.overrides.pop(point.id, None)
        until = self.overrides.get(point.id, {}).get("endsAt") if mode == "hand" else None
        summary = (f"hand mode on {point.name}: {_text(value)} for {hours:g} h" if mode == "hand"
                   else f"set {point.name} to {_text(value)}")
        await self._audit(actor, "command", summary, point, {"value": before},
                          {"value": value, "mode": mode, **({"until": until} if until else {})})
        self._publish(point.id)
        return {"ok": True, "point": point.name, "value": value, "mode": mode,
                "override": self.overrides.get(point.id), "readBack": await self._read_back(point)}

    async def release(self, actor: Actor, point_id: uuid.UUID) -> dict:
        """Release to auto: end hand mode now, or (BACnet) release the operator priority."""
        point = self.point(point_id)
        if not point.writable:
            raise CommandError("this point cannot be commanded")
        async with self.sessions() as session:
            row = await session.get(db.PointOverride, point.id)
        if row is None and not isinstance(point.address, BacnetAddress):
            raise CommandError("this point is not in hand mode")
        await self._end(point, row, actor, "released to auto")
        return {"ok": True, "point": point.name, "readBack": await self._read_back(point)}

    async def _end(self, point: RuntimePoint, row: db.PointOverride | None, actor: Actor, why: str) -> None:
        lock = self._locks.setdefault(point.id, asyncio.Lock())
        async with lock:
            before = self._current(point)
            if isinstance(point.address, BacnetAddress):
                value, how = None, "released the operator priority"
            else:
                scheduled = self.schedule_value(point)
                previous = row.previous.get("v") if row else None
                value = scheduled if scheduled is not None else previous
                how = (f"wrote the schedule's value {_text(value)}" if scheduled is not None
                       else f"wrote back {_text(value)}" if value is not None else "nothing to write back")
            try:
                if value is not None or isinstance(point.address, BacnetAddress):
                    await write_point(self.runtime, point, value, MANUAL_PRIORITY)
            except WriteError as exc:
                await self._audit(actor, "command-failed", f"returning {point.name} to auto failed: {exc}", point,
                                  {"value": before}, None)
                raise CommandError(f"the write failed: {exc}", 502) from exc
            if row is not None:
                async with self.sessions() as session, session.begin():
                    await session.execute(delete(db.PointOverride).where(db.PointOverride.point_id == point.id))
                self.overrides.pop(point.id, None)
        await self._audit(actor, "release", f"{point.name} {why}: {how}", point, {"value": before},
                          {"value": value})
        self._publish(point.id)

    async def _expiry_loop(self) -> None:
        while True:
            try:
                await self.expire()
            except Exception:
                log.exception("override timer check failed")
            await asyncio.sleep(self.check_s)

    async def expire(self) -> None:
        now = datetime.now(UTC)
        async with self.sessions() as session:
            rows = (await session.scalars(select(db.PointOverride).where(db.PointOverride.ends_at <= now))).all()
        for row in rows:
            point = next((p for p in self.runtime.config.points if p.id == row.point_id), None)
            if point is None:  # removed or disabled meanwhile: nothing to write to
                async with self.sessions() as session, session.begin():
                    await session.execute(delete(db.PointOverride).where(db.PointOverride.point_id == row.point_id))
                self.overrides.pop(row.point_id, None)
                continue
            try:
                await self._end(point, row, TIMER, "override time ended")
            except CommandError as exc:
                log.warning("override on %s could not end yet: %s", point.name, exc.message)  # tried again

    async def _read_back(self, point: RuntimePoint) -> dict | None:
        """Read the point from the device now (after a write, so the screens show what the device
        holds). A Good answer also becomes the point's live value."""
        if not hasattr(point.address, "object_type"):
            return None  # MQTT: the device reports the new state on its own topic
        from devine_bms.setup.service import SetupError
        from devine_bms.setup.testread import test_read

        device = next(d for d in self.runtime.config.devices if d.id == point.device_id)
        try:
            out = await test_read(self.runtime, None, {"device": device.name, "points": [point.name]})
            r = out["results"][0]
            tracker = getattr(self.runtime, "tracker", None)
            if r["status"] == "ok" and tracker is not None:
                tracker.good(point, r["value"])
            return {"status": r["status"], "value": r["value"], "error": r.get("error")}
        except (SetupError, Exception) as exc:  # the write itself succeeded; the read is a courtesy
            return {"status": "unknown", "value": None, "error": str(exc)}

    async def _audit(self, actor: Actor, action: str, summary: str, point: RuntimePoint | None, before: Any,
                     after: Any) -> None:
        if self.record is not None:
            await self.record(actor, action, summary, kind="point", name=point.name if point else None,
                              before=before, after=after)


def _text(value: Any) -> str:
    if value is True:
        return "on"
    if value is False:
        return "off"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)
