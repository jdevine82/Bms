"""Setup changes (milestone 5A).

Every change goes the same way: export the current setup, apply the change to it,
check the whole result with the config file's checks, and only then save it, record
it in the change log and reload the runtime. A change that fails a check is refused
with the list of errors; warnings are returned and allowed.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from devine_bms.config.sync import sync_config
from devine_bms.config.validate import ConfigError, Issue, parse_config
from devine_bms.db import models as db
from devine_bms.setup.export import export_dict

log = logging.getLogger(__name__)

KINDS = {"spaces": db.Space, "equips": db.Equip, "buses": db.Bus, "devices": db.Device, "points": db.Point}
SINGULAR = {"spaces": "space", "equips": "equip", "buses": "bus", "devices": "device", "points": "point"}


class SetupError(Exception):
    """A refused change. `status` is the HTTP status the API answers with."""

    def __init__(self, message: str, status: int = 422, issues: list[Issue] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.issues = issues or []

    def as_dict(self) -> dict:
        return {"ok": False, "error": self.message,
                "issues": [{"severity": i.severity.value, "where": i.where, "message": i.message}
                           for i in self.issues]}


def _issues(issues: list[Issue]) -> list[dict]:
    return [{"severity": i.severity.value, "where": i.where, "message": i.message} for i in issues]


class SetupService:
    def __init__(self, sessions: async_sessionmaker, site_name: str,
                 reload: Callable[[], Awaitable[None]] | None = None) -> None:
        self.sessions = sessions
        self.site_name = site_name
        self.reload = reload

    async def export(self) -> dict:
        async with self.sessions() as session:
            return await export_dict(session, self.site_name)

    # ---------- whole setup ----------

    async def import_config(self, data: dict, client: str, check_only: bool = False) -> dict:
        if isinstance(data.get("site"), dict) and data["site"].get("name") != self.site_name:
            raise SetupError(f"the file is for site '{data['site'].get('name')}', not '{self.site_name}'; "
                             "the site name cannot change")
        before = await self.export()
        current = {p["name"] for p in before.get("points", [])}
        for point in data.get("points", []) if isinstance(data.get("points"), list) else []:
            if isinstance(point, dict) and point.get("name") not in current:
                await self._refuse_alias(point.get("name"), None)
        return await self._apply(data, client, "import", None, None, "imported a whole site setup",
                                 before, data, check_only)

    async def put_site(self, site: dict, bacnet: dict | None, client: str, check_only: bool = False) -> dict:
        data = await self.export()
        if site.get("name", self.site_name) != self.site_name:
            raise SetupError("the site name cannot change")
        before = {"site": data["site"], "bacnet": data.get("bacnet")}
        data["site"] = {**site, "name": self.site_name}
        if bacnet is None:
            data.pop("bacnet", None)
        else:
            data["bacnet"] = bacnet
        return await self._apply(data, client, "put", "site", self.site_name, "changed the site settings",
                                 before, {"site": data["site"], "bacnet": bacnet}, check_only)

    async def put_feeds(self, feeds: list[dict], client: str, check_only: bool = False) -> dict:
        data = await self.export()
        before = data["feeds"]
        data["feeds"] = feeds
        return await self._apply(data, client, "put", "feeds", None, f"set {len(feeds)} feeds", before, feeds,
                                 check_only)

    # ---------- one item ----------

    async def put_item(self, kind: str, name: str, item: dict, client: str, check_only: bool = False) -> dict:
        self._kind(kind)
        if item.get("name", name) != name:
            raise SetupError("the name in the body differs from the address; use rename to change a name")
        item = {**item, "name": name}
        data = await self.export()
        items = data.setdefault(kind, [])
        before = next((i for i in items if i["name"] == name), None)
        if before is None and kind == "points":
            await self._refuse_alias(name, None)
        if before is None:
            items.append(item)
            summary = f"added {SINGULAR[kind]} {name}"
        else:
            items[items.index(before)] = item
            summary = f"changed {SINGULAR[kind]} {name}"
        return await self._apply(data, client, "put", SINGULAR[kind], name, summary, before, item, check_only)

    async def delete_item(self, kind: str, name: str, client: str, check_only: bool = False) -> dict:
        self._kind(kind)
        data = await self.export()
        before = next((i for i in data.get(kind, []) if i["name"] == name), None)
        if before is None:
            raise SetupError(f"no {SINGULAR[kind]} '{name}'", status=404)
        data[kind].remove(before)
        return await self._apply(data, client, "delete", SINGULAR[kind], name, f"removed {SINGULAR[kind]} {name}",
                                 before, None, check_only)

    async def rename_item(self, kind: str, name: str, new_name: str, client: str, check_only: bool = False) -> dict:
        """Rename and keep the item's ID; the config file's references follow (main spec 5.2)."""
        table = self._kind(kind)
        data = await self.export()
        if not any(i["name"] == name for i in data.get(kind, [])):
            raise SetupError(f"no {SINGULAR[kind]} '{name}'", status=404)
        renamed = _rename_in(copy.deepcopy(data), kind, name, new_name)
        warnings = self._check(renamed)
        if check_only:
            return {"ok": True, "applied": False, "warnings": warnings}
        async with self.sessions() as session, session.begin():
            site = await self._site(session)
            row = await session.scalar(select(table).where(table.site_id == site.id, table.name == name))
            clash = await session.scalar(select(table).where(table.site_id == site.id, table.name == new_name))
            if clash is not None:
                raise SetupError(f"a removed {SINGULAR[kind]} is still called '{new_name}' (kept for its history); "
                                 "pick another name")
            if kind == "points":
                await self._refuse_alias(new_name, row.id, session)
                # Taking back one of its own old names: that name is no longer an alias.
                own = await session.scalar(select(db.PointAlias).where(
                    db.PointAlias.site_id == site.id, db.PointAlias.name == new_name))
                if own is not None:
                    await session.delete(own)
                session.add(db.PointAlias(site_id=site.id, point_id=row.id, name=name))
            row.name = new_name
            self._log(session, site.id, client, "rename", SINGULAR[kind], new_name,
                      f"renamed {SINGULAR[kind]} {name} to {new_name}", {"name": name}, {"name": new_name})
            try:
                await session.flush()
            except IntegrityError as exc:
                raise SetupError(f"'{new_name}' is already used, also as an old point name") from exc
        await self._reload()
        return {"ok": True, "applied": True, "warnings": warnings}

    # ---------- change log ----------

    async def changes(self, limit: int = 50) -> list[dict]:
        async with self.sessions() as session:
            site = await self._site(session)
            rows = (await session.scalars(
                select(db.SetupChange).where(db.SetupChange.site_id == site.id)
                .order_by(db.SetupChange.ts.desc()).limit(limit)
            )).all()
        return [{"ts": r.ts.isoformat(), "client": r.client, "action": r.action, "kind": r.kind, "name": r.name,
                 "summary": r.summary, "before": r.before, "after": r.after} for r in rows]

    async def record(self, client: str, action: str, summary: str, kind: str | None = None,
                     name: str | None = None, after: Any = None) -> None:
        """Record something that is not a setup change but belongs in the log, such as a scan."""
        async with self.sessions() as session, session.begin():
            site = await self._site(session)
            self._log(session, site.id, client, action, kind, name, summary, None, after)

    # ---------- internals ----------

    async def _refuse_alias(self, name: str, point_id, session: AsyncSession | None = None) -> None:
        """A point name may not be another point's old name: old CSV files and logic resolve old names."""
        async def check(s: AsyncSession) -> None:
            site = await self._site(s)
            alias = await s.scalar(select(db.PointAlias).where(
                db.PointAlias.site_id == site.id, db.PointAlias.name == name))
            if alias is not None and alias.point_id != point_id:
                owner = await s.get(db.Point, alias.point_id)
                raise SetupError(f"'{name}' is an old name of point '{owner.name}' and still points to it; "
                                 "pick another name")
        if session is not None:
            await check(session)
        else:
            async with self.sessions() as s:
                await check(s)

    def _kind(self, kind: str):
        if kind not in KINDS:
            raise SetupError(f"unknown kind '{kind}'; use one of {', '.join(KINDS)}", status=404)
        return KINDS[kind]

    def _check(self, data: dict) -> list[dict]:
        try:
            _, warnings = parse_config(data)
        except ConfigError as exc:
            raise SetupError("the change was refused: it fails the setup checks", issues=exc.issues) from exc
        return _issues(warnings)

    async def _apply(self, data: dict, client: str, action: str, kind: str | None, name: str | None,
                     summary: str, before: Any, after: Any, check_only: bool) -> dict:
        try:
            config, warnings = parse_config(data)
        except ConfigError as exc:
            raise SetupError("the change was refused: it fails the setup checks", issues=exc.issues) from exc
        if check_only:
            return {"ok": True, "applied": False, "warnings": _issues(warnings)}
        async with self.sessions() as session, session.begin():
            report = await sync_config(session, config)
            self._log(session, report.site_id, client, action, kind, name, summary, before, after)
        await self._reload()
        return {"ok": True, "applied": True, "warnings": _issues(warnings),
                "report": {"created": report.created, "updated": report.updated, "disabled": report.disabled}}

    async def _reload(self) -> None:
        if self.reload is not None:
            await self.reload()

    async def _site(self, session: AsyncSession) -> db.Site:
        site = await session.scalar(select(db.Site).where(db.Site.name == self.site_name))
        if site is None:
            raise SetupError(f"no site '{self.site_name}' in the database", status=404)
        return site

    @staticmethod
    def _log(session: AsyncSession, site_id, client: str, action: str, kind: str | None, name: str | None,
             summary: str, before: Any, after: Any) -> None:
        session.add(db.SetupChange(site_id=site_id, client=client[:64], action=action, kind=kind, name=name,
                                   summary=summary, before=before, after=after))
        log.info("setup change by %s: %s", client, summary)


def _rename_in(data: dict, kind: str, old: str, new: str) -> dict:
    """Rename an item in a config document, and every reference to it."""
    for item in data.get(kind, []):
        if item["name"] == old:
            item["name"] = new
    if kind == "spaces":
        for e in data.get("equips", []):
            if e.get("space") == old:
                e["space"] = new
        for p in data.get("points", []):
            if p.get("space") == old:
                p["space"] = new
        for f in data.get("feeds", []):
            if f.get("to") == old:
                f["to"] = new
    elif kind == "equips":
        for e in data.get("equips", []):
            if e.get("parent") == old:
                e["parent"] = new
        for p in data.get("points", []):
            if p.get("equip") == old:
                p["equip"] = new
        for f in data.get("feeds", []):
            for end in ("from", "to"):
                if f.get(end) == old:
                    f[end] = new
    elif kind == "buses":
        for d in data.get("devices", []):
            for field in ("bus", "trunk", "broker"):
                if d.get("connection", {}).get(field) == old:
                    d["connection"][field] = new
    elif kind == "devices":
        for p in data.get("points", []):
            if p.get("device") == old:
                p["device"] = new
    return data
