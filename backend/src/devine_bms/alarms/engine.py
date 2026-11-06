"""The alarm engine (main spec 5.5).

Every `tick_s` it looks at the current value of each point that has alarms, and at each device
that has an offline alarm, and raises or returns alarms:

- high and low limits with a deadband, and states, only on Good values: a Bad or Stale value
  neither raises nor clears them (main spec 5.2.1), so one fault does not cause a flood;
- stale: while the point's value is Stale;
- device offline: while every point of the device is down (it stopped answering);
- a delay: the condition must hold this long before the alarm is raised.

An occurrence is one row in `alarm`. It is closed once it is acknowledged and back to normal.
If it comes back before it is acknowledged, the same row becomes active again (and nobody is
notified a second time); after it is closed, the next one is a new occurrence and notifies.
Alarms whose definition is removed (or whose point or device is disabled) return to normal.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select

from devine_bms.config.model import OfflineAlarm, PointAlarm
from devine_bms.db import models as db
from devine_bms.values import Status

log = logging.getLogger(__name__)

LEVEL_ORDER = {"critical": 0, "warning": 1, "info": 2}


def alarm_json(a: db.Alarm) -> dict:
    return {"id": str(a.id), "key": a.key, "source": a.source, "sourceDis": a.source_dis, "type": a.type,
            "level": a.level, "message": a.message, "detail": a.detail, "active": a.active, "closed": a.closed,
            "raisedAt": a.raised_at.isoformat(), "returnedAt": a.returned_at.isoformat() if a.returned_at else None,
            "ackedAt": a.acked_at.isoformat() if a.acked_at else None, "ackedBy": a.acked_by,
            "pointId": str(a.point_id) if a.point_id else None, "deviceId": str(a.device_id) if a.device_id else None}


@dataclass
class Definition:
    key: str
    kind: str  # high, low, state, stale, offline
    level: str
    delay_s: float
    message: str
    source: str
    source_dis: str
    point_id: uuid.UUID | None = None
    device_id: uuid.UUID | None = None
    alarm: PointAlarm | None = None
    unit: str | None = None


def _state_text(state: Any) -> str:
    if state is True:
        return "on"
    if state is False:
        return "off"
    return str(state)


def default_message(kind: str, dis: str, alarm: PointAlarm | None) -> str:
    if kind == "high":
        return f"{dis} high"
    if kind == "low":
        return f"{dis} low"
    if kind == "state":
        return f"{dis} is {_state_text(alarm.state)}"
    if kind == "stale":
        return f"{dis}: no fresh value"
    return f"{dis} is not answering"


class AlarmEngine:
    def __init__(self, runtime, sessions, site_name: str,
                 on_raise: Callable[[dict], Awaitable[None]] | None = None, tick_s: float = 1.0) -> None:
        self.runtime = runtime
        self.sessions = sessions
        self.site_name = site_name
        self.on_raise = on_raise
        self.tick_s = tick_s
        self.open: dict[str, db.Alarm] = {}  # key -> the open (not closed) occurrence
        self.pending: dict[str, float] = {}  # key -> monotonic time the condition began
        self.subscribers: set[asyncio.Queue] = set()
        self.lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._site_id: uuid.UUID | None = None

    # ---------- life ----------

    async def start(self) -> None:
        async with self.sessions() as session:
            site = await session.scalar(select(db.Site).where(db.Site.name == self.site_name))
            self._site_id = site.id
            rows = (await session.scalars(select(db.Alarm).where(db.Alarm.site_id == site.id,
                                                                  db.Alarm.closed.is_(False)))).all()
        self.open = {r.key: r for r in rows}
        self._task = asyncio.create_task(self._loop(), name="alarm engine")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                await self.evaluate()
            except Exception:  # keep running; one bad tick must not stop alarms
                log.exception("alarm engine tick failed")
            await asyncio.sleep(self.tick_s)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    def _publish(self, row: db.Alarm) -> None:
        data = alarm_json(row)
        for q in list(self.subscribers):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass

    # ---------- definitions ----------

    def definitions(self) -> dict[str, Definition]:
        cfg = self.runtime.config
        devices = {d.id: d for d in cfg.devices}
        out: dict[str, Definition] = {}
        for p in cfg.points:
            device = devices.get(p.device_id)
            if device is None or not device.enabled:
                continue
            for a in p.alarms:
                key = f"point:{p.id}:{a.key}"
                out[key] = Definition(key, a.type, a.level.value, a.delay_s,
                                      a.message or default_message(a.type, p.dis or p.name, a),
                                      p.name, p.dis or p.name, point_id=p.id, device_id=p.device_id, alarm=a,
                                      unit=p.unit)
        for d in cfg.devices:
            off: OfflineAlarm | None = d.offline_alarm
            if off is None or not d.enabled:
                continue
            key = f"device:{d.id}:offline"
            out[key] = Definition(key, "offline", off.level.value, off.delay_s,
                                  off.message or default_message("offline", d.dis or d.name, None),
                                  d.name, d.dis or d.name, device_id=d.id)
        return out

    def _condition(self, defn: Definition, active: bool) -> tuple[bool | None, dict]:
        """True: in alarm; False: normal; None: cannot tell (Bad or not read yet)."""
        cache = self.runtime.cache
        if defn.kind == "offline":
            statuses = [cache.get(p.id).status if cache.get(p.id) else Status.UNKNOWN
                        for p in self.runtime.config.points if p.device_id == defn.device_id]
            if not statuses:
                return None, {}
            if all(s == Status.DOWN for s in statuses):
                return True, {}
            if any(s in (Status.OK, Status.STALE) for s in statuses):
                return False, {}
            return None, {}
        live = cache.get(defn.point_id)
        if live is None:
            return None, {}
        a = defn.alarm
        if defn.kind == "stale":
            if live.status == Status.STALE:
                return True, {}
            return (False, {}) if live.status == Status.OK else (None, {})
        if live.status != Status.OK:
            return None, {}
        value = live.value
        detail = {"value": value, "unit": defn.unit}
        if defn.kind in ("high", "low"):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return None, detail
            detail["limit"] = a.limit
            if defn.kind == "high":
                edge = a.limit - a.deadband if active else a.limit
                return value > edge, detail
            edge = a.limit + a.deadband if active else a.limit
            return value < edge, detail
        detail["state"] = a.state
        if isinstance(a.state, str) and isinstance(value, str):
            return value.lower() == a.state.lower(), detail
        return value == a.state, detail

    # ---------- the tick ----------

    async def evaluate(self) -> None:
        async with self.lock:
            defs = self.definitions()
            now = time.monotonic()
            for key, defn in defs.items():
                row = self.open.get(key)
                active = bool(row and row.active)
                cond, detail = self._condition(defn, active)
                if cond is True and not active:
                    began = self.pending.setdefault(key, now)
                    if now - began >= defn.delay_s:
                        self.pending.pop(key, None)
                        await self._raise(defn, detail)
                elif cond is False:
                    self.pending.pop(key, None)
                    if active:
                        await self._return(row, "back to normal")
            for key in [k for k in self.pending if k not in defs]:
                self.pending.pop(key, None)
            for key, row in list(self.open.items()):
                if key not in defs and row.active:
                    await self._return(row, "the alarm was removed from the setup, or its point or device disabled")

    async def _raise(self, defn: Definition, detail: dict) -> None:
        now = datetime.now(UTC)
        row = self.open.get(defn.key)
        new = row is None
        async with self.sessions() as session, session.begin():
            if new:
                row = db.Alarm(site_id=self._site_id, key=defn.key, point_id=defn.point_id, device_id=defn.device_id,
                               source=defn.source, source_dis=defn.source_dis, type=defn.kind, level=defn.level,
                               message=defn.message, detail=_jsonable(detail), active=True, closed=False,
                               raised_at=now)
                session.add(row)
                await session.flush()
                session.add(db.AlarmEvent(alarm_id=row.id, ts=now, kind="raised", text=_value_text(detail)))
            else:
                row = await session.merge(row)
                row.active, row.returned_at, row.detail = True, None, _jsonable(detail)
                row.level, row.message = defn.level, defn.message
                session.add(db.AlarmEvent(alarm_id=row.id, ts=now, kind="raised",
                                          text="raised again before it was acknowledged" +
                                          (f"; {_value_text(detail)}" if detail else "")))
        self.open[defn.key] = row
        log.info("alarm raised: %s %s", row.level, row.message)
        self._publish(row)
        if new and self.on_raise is not None:
            try:
                await self.on_raise(alarm_json(row))
            except Exception:  # delivery problems never stop the engine
                log.exception("queueing notifications failed")

    async def _return(self, row: db.Alarm, why: str) -> None:
        now = datetime.now(UTC)
        async with self.sessions() as session, session.begin():
            row = await session.merge(row)
            row.active, row.returned_at = False, now
            if row.acked_at is not None:
                row.closed = True
            session.add(db.AlarmEvent(alarm_id=row.id, ts=now, kind="returned", text=why))
        self._settle(row)

    def _settle(self, row: db.Alarm) -> None:
        if row.closed:
            self.open.pop(row.key, None)
        else:
            self.open[row.key] = row
        self._publish(row)

    # ---------- people ----------

    async def acknowledge(self, alarm_ids: list[uuid.UUID], username: str, comment: str | None) -> list[dict]:
        now = datetime.now(UTC)
        done = []
        async with self.lock:
            async with self.sessions() as session, session.begin():
                rows = (await session.scalars(select(db.Alarm).where(db.Alarm.id.in_(alarm_ids),
                                                                      db.Alarm.site_id == self._site_id))).all()
                for row in rows:
                    if row.acked_at is not None:
                        continue
                    row.acked_at, row.acked_by = now, username
                    if not row.active:
                        row.closed = True
                    session.add(db.AlarmEvent(alarm_id=row.id, ts=now, kind="acked", username=username,
                                              text=comment or None))
                    done.append(row)
            for row in done:
                self._settle(row)
        return [alarm_json(r) for r in done]

    async def comment(self, alarm_id: uuid.UUID, username: str, text: str) -> None:
        async with self.sessions() as session, session.begin():
            row = await session.scalar(select(db.Alarm).where(db.Alarm.id == alarm_id,
                                                               db.Alarm.site_id == self._site_id))
            if row is None:
                raise KeyError(str(alarm_id))
            session.add(db.AlarmEvent(alarm_id=row.id, kind="comment", username=username, text=text))

    async def prune(self, keep_days: int = 365) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=keep_days)
        async with self.sessions() as session, session.begin():
            result = await session.execute(delete(db.Alarm).where(db.Alarm.closed.is_(True),
                                                                   db.Alarm.raised_at < cutoff))
            await session.execute(delete(db.AlarmDelivery).where(db.AlarmDelivery.created_at < cutoff))
            return result.rowcount or 0


def _jsonable(detail: dict) -> dict:
    return {k: v for k, v in detail.items() if isinstance(v, (str, int, float, bool)) or v is None}


def _value_text(detail: dict) -> str:
    if "value" not in detail:
        return ""
    value = detail["value"]
    text = f"{round(value, 2) if isinstance(value, float) else _state_text(value) if isinstance(value, bool) else value}"
    if detail.get("unit"):
        text += f" {detail['unit']}"
    if detail.get("limit") is not None:
        text += f", limit {detail['limit']}"
    return f"value {text}"

