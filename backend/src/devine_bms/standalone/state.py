"""The standalone state file (milestone 11a, decision 028).

In standalone mode PostgreSQL runs in RAM. Everything a site keeps through a power cut is
written to one JSON file on the config partition after each saved change, and put back at boot:

    {"schema": "devine-bms-state/1", "savedAt": ..., "config": <the setup as an export>,
     "tables": {<table>: [<row>, ...]}, "standalone": {<standalone settings>}}

`config` is the export (the upgrade path to full mode); `tables` restores the exact rows. The
log, live values, alarms, the audit log, login sessions and hand-mode overrides are not kept.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shlex
import subprocess
import tempfile
import time
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Date,
    DateTime,
    LargeBinary,
    delete,
    event,
    insert,
    inspect,
    select,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session

from devine_bms.db import models as db

log = logging.getLogger(__name__)

SCHEMA = "devine-bms-state/1"
DEBOUNCE_S = 2.0
DAILY_S = 24 * 3600

# Kept through a power cut, parents before children.
KEPT = [db.Site, db.Space, db.Equip, db.EquipFeed, db.Bus, db.Device, db.Point, db.PointAlias, db.User,
        db.DeviceTemplateRow, db.HolidayCalendar, db.Schedule, db.LogicDiagram, db.LogicVersion,
        db.LogicVariable, db.LogicInstance, db.EquipCounter]
KEPT_TABLES = {m.__tablename__ for m in KEPT}
# Changes that alone do not make a save worth a write to the card.
QUIET = {
    "app_user": {"failed_logins", "locked_until", "last_login", "updated_at"},
    "site": {"updated_at"},
    # Counting changes all the time: saved daily and on stop; a service or a reminder saves at once (5.10).
    "equip_counter": {"run_s", "starts", "unseen_s", "last_state", "updated_at"},
}


def _json(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, bytes):
        return base64.b64encode(value).decode()
    return value


def _from_json(column, value: Any) -> Any:
    if value is None:
        return None
    t = column.type
    if isinstance(t, UUID):
        return uuid.UUID(value)
    if isinstance(t, DateTime):
        return datetime.fromisoformat(value)
    if isinstance(t, Date):
        return date.fromisoformat(value)
    if isinstance(t, LargeBinary):
        return base64.b64decode(value)
    return value


async def dump(sessions, site_name: str, standalone: dict | None = None) -> dict:
    from devine_bms.setup.export import export_dict

    async with sessions() as session:
        config = await export_dict(session, site_name)
        tables: dict[str, list[dict]] = {}
        for model in KEPT:
            cols = [c for c in model.__table__.columns]
            rows = (await session.execute(select(model.__table__))).mappings().all()
            tables[model.__tablename__] = [{c.name: _json(r[c.name]) for c in cols} for r in rows]
    return {"schema": SCHEMA, "savedAt": datetime.now(UTC).isoformat(), "config": config, "tables": tables,
            "standalone": standalone or {}}


async def restore(sessions, state: dict) -> str:
    """Put a state file back into an empty database. Returns the site's name."""
    if state.get("schema") != SCHEMA:
        raise ValueError(f"not a {SCHEMA} file")
    tables = state.get("tables") or {}
    async with sessions() as session, session.begin():
        for model in KEPT:
            rows = tables.get(model.__tablename__) or []
            if not rows:
                continue
            cols = {c.name: c for c in model.__table__.columns}
            values = [{k: _from_json(cols[k], v) for k, v in row.items() if k in cols} for row in rows]
            await session.execute(insert(model.__table__), values)
    sites = tables.get("site") or []
    if not sites:
        raise ValueError("the state file has no site")
    return sites[0]["name"]


def write_atomic(path: Path, text: str, rw_cmd: str = "", ro_cmd: str = "") -> None:
    """Write the file so a power cut leaves the old or the new one, never half of either. On the
    Pi the config partition is read-only except during the write."""
    if rw_cmd:
        subprocess.run(shlex.split(rw_cmd), check=True, timeout=30)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".state-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            dirfd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    finally:
        if ro_cmd:
            subprocess.run(shlex.split(ro_cmd), check=False, timeout=30)


class StateKeeper:
    """Saves the state file after kept tables change (2 seconds after the last change), once a
    day, and on stop."""

    def __init__(self, sessions, site_name: str, path: Path, rw_cmd: str = "", ro_cmd: str = "",
                 debounce_s: float = DEBOUNCE_S, daily_s: float = DAILY_S) -> None:
        self.sessions = sessions
        self.site_name = site_name
        self.path = path
        self.rw_cmd = rw_cmd
        self.ro_cmd = ro_cmd
        self.debounce_s = debounce_s
        self.daily_s = daily_s
        self.standalone: dict = {}
        self.dirty_at: float | None = None
        self.saves = 0
        self.last_saved: datetime | None = None
        self.last_error: str | None = None
        self._task: asyncio.Task | None = None
        self._listener = None

    def start(self) -> None:
        keeper = self

        def after_flush(session: Session, _ctx) -> None:
            if keeper._changed(session):
                session.info["bms_state_dirty"] = True

        def after_commit(session: Session) -> None:
            if session.info.pop("bms_state_dirty", False):
                keeper.mark()

        def after_rollback(session: Session) -> None:
            session.info.pop("bms_state_dirty", None)

        event.listen(Session, "after_flush", after_flush)
        event.listen(Session, "after_commit", after_commit)
        event.listen(Session, "after_soft_rollback", after_rollback)
        self._listener = (after_flush, after_commit, after_rollback)
        self._task = asyncio.create_task(self._loop(), name="state file")

    async def stop(self) -> None:
        if self._listener:
            for name, fn in zip(("after_flush", "after_commit", "after_soft_rollback"), self._listener, strict=True):
                event.remove(Session, name, fn)
            self._listener = None
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        await self.save("stop")

    def mark(self) -> None:
        self.dirty_at = time.monotonic()

    @staticmethod
    def _changed(session: Session) -> bool:
        for obj in list(session.new) + list(session.deleted):
            if getattr(obj, "__tablename__", None) in KEPT_TABLES:
                return True
        for obj in session.dirty:
            table = getattr(obj, "__tablename__", None)
            if table not in KEPT_TABLES:
                continue
            if table == "logic_variable" and getattr(obj, "kind", "") == "memory":
                continue  # logic writes these all the time: kept by the daily save
            state = inspect(obj)
            changed = {a.key for a in state.attrs if a.history.has_changes()}
            if changed - QUIET.get(table, set()):
                return True
        return False

    async def _loop(self) -> None:
        last_daily = time.monotonic()
        while True:
            await asyncio.sleep(0.25 if self.debounce_s < 1 else 0.5)
            now = time.monotonic()
            if self.dirty_at is not None and now - self.dirty_at >= self.debounce_s:
                self.dirty_at = None
                await self.save("change")
            elif now - last_daily >= self.daily_s:
                last_daily = now
                await self.save("daily")

    async def save(self, why: str) -> bool:
        try:
            state = await dump(self.sessions, self.site_name, self.standalone)
            text = json.dumps(state, indent=1, ensure_ascii=False)
            await asyncio.to_thread(write_atomic, self.path, text, self.rw_cmd, self.ro_cmd)
        except Exception as exc:  # noqa: BLE001 -- shown on the screens; tried again on the next change
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.error("cannot save the state file %s (%s): %s", self.path, why, exc)
            return False
        self.saves += 1
        self.last_saved = datetime.now(UTC)
        self.last_error = None
        log.info("saved the state file (%s)", why)
        return True


async def clear_kept(sessions) -> None:
    """Remove the kept rows (tests and re-provisioning)."""
    async with sessions() as session, session.begin():
        for model in reversed(KEPT):
            await session.execute(delete(model.__table__))
