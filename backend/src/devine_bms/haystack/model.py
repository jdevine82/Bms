"""The site as Haystack 4 entities (milestone 12a-Haystack; main spec 5.2 and 5.12).

site, space (rooms), equip and point entities with their tags, refs and live values. Ids are
the permanent UUIDs (`@<uuid>`, with `dis`). Built from the database on each request; live
values come from the runtime's cache, never from the field buses.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from devine_bms.db import models as db
from devine_bms.haystack.codec import from_hayson, to_datetime
from devine_bms.haystack.kinds import MARKER, Coord, Number, Ref
from devine_bms.runtime_model import point_writable
from devine_bms.storage.postgres import _address
from devine_bms.values import Status

# The "feeds" mediums (main spec 5.2) as the Haystack ref on the thing fed.
FEED_REF = {"air": "airRef", "chilledWater": "chilledWaterRef", "hotWater": "hotWaterRef",
            "condenserWater": "condenserWaterRef", "elec": "elecRef", "refrig": "refrigRef"}


def tag_value(v: Any) -> Any:
    """A tag as stored (True for a marker, a string or a number) as a Haystack value."""
    if v is True:
        return MARKER
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return Number(float(v))
    if isinstance(v, dict) and "_kind" in v:
        return from_hayson(v)
    return v


@dataclass
class Model:
    tz: str
    entities: dict[str, dict] = field(default_factory=dict)  # id -> entity, in nav order
    children: dict[str | None, list[str]] = field(default_factory=dict)  # navId -> child ids
    kinds: dict[str, str] = field(default_factory=dict)  # id -> site, space, equip or point
    point_kind: dict[str, str] = field(default_factory=dict)  # point id -> Number, Bool or Str

    def get(self, ref_id: str) -> dict | None:
        return self.entities.get(ref_id)


def _ref(obj_id: uuid.UUID, dis: str | None = None) -> Ref:
    return Ref(str(obj_id), dis)


async def build_model(sessions, site_name: str, runtime=None, live: bool = True) -> Model:
    async with sessions() as session:
        site = await session.scalar(select(db.Site).where(db.Site.name == site_name))
        spaces = (await session.scalars(select(db.Space).where(db.Space.site_id == site.id, db.Space.enabled.is_(True))
                                        .order_by(db.Space.name))).all()
        equips = (await session.scalars(select(db.Equip).where(db.Equip.site_id == site.id, db.Equip.enabled.is_(True))
                                        .order_by(db.Equip.name))).all()
        feeds = (await session.scalars(select(db.EquipFeed).where(db.EquipFeed.site_id == site.id))).all()
        devices = {d.id: d for d in (await session.scalars(select(db.Device).where(db.Device.site_id == site.id))).all()}
        points = (await session.scalars(select(db.Point).where(db.Point.site_id == site.id, db.Point.enabled.is_(True))
                                        .order_by(db.Point.name))).all()
    tz = site.tz or "UTC"
    m = Model(tz=tz)
    site_ref = _ref(site.id, site.dis)
    s = {"id": site_ref, "dis": site.dis, "site": MARKER, "tz": tz}
    if site.geo_lat is not None and site.geo_lng is not None:
        s["geoCoord"] = Coord(site.geo_lat, site.geo_lng)
    if site.area_m2:
        s["area"] = Number(site.area_m2, "m²")
    s.update({k: tag_value(v) for k, v in (site.tags or {}).items() if k not in s})
    _add(m, None, "site", s)

    space_refs = {sp.id: _ref(sp.id, sp.dis) for sp in spaces}
    equip_refs = {e.id: _ref(e.id, e.dis) for e in equips}
    fed: dict[uuid.UUID, dict[str, Ref]] = {}
    for f in sorted(feeds, key=lambda f: str(f.from_equip_id)):
        target = f.to_equip_id or f.to_space_id
        tag = FEED_REF.get(f.medium)
        if tag and f.from_equip_id in equip_refs:
            fed.setdefault(target, {}).setdefault(tag, equip_refs[f.from_equip_id])  # one feeder a medium

    for sp in spaces:
        e = {"id": space_refs[sp.id], "dis": sp.dis, "space": MARKER, "siteRef": site_ref}
        e.update(fed.get(sp.id, {}))
        e.update({k: tag_value(v) for k, v in (sp.tags or {}).items() if k not in e})
        _add(m, str(site.id), "space", e)

    equip_space = {}
    by_parent: dict[uuid.UUID | None, list[db.Equip]] = {}
    for eq in equips:
        by_parent.setdefault(eq.parent_equip_id if eq.parent_equip_id in equip_refs else None, []).append(eq)

    def add_equip(eq: db.Equip, nav_parent: str) -> None:
        space_id = eq.space_id if eq.space_id in space_refs else None
        if space_id is None and eq.parent_equip_id in equip_space:
            space_id = equip_space[eq.parent_equip_id]  # inside its parent's room
        equip_space[eq.id] = space_id
        e = {"id": equip_refs[eq.id], "dis": eq.dis, "equip": MARKER, "siteRef": site_ref}
        if space_id:
            e["spaceRef"] = space_refs[space_id]
        if eq.parent_equip_id in equip_refs:
            e["equipRef"] = equip_refs[eq.parent_equip_id]
        e.update(fed.get(eq.id, {}))
        e.update({k: tag_value(v) for k, v in (eq.tags or {}).items() if k not in e})
        _add(m, nav_parent, "equip", e)
        for child in by_parent.get(eq.id, []):
            add_equip(child, str(eq.id))

    for eq in by_parent.get(None, []):
        add_equip(eq, str(eq.space_id) if eq.space_id in space_refs else str(site.id))

    cache = getattr(runtime, "cache", None) if live else None
    for p in points:
        if p.device_id not in devices:
            continue
        e: dict[str, Any] = {"id": _ref(p.id, p.dis), "dis": p.dis, "point": MARKER, p.point_type: MARKER,
                             "kind": p.kind, "siteRef": site_ref, "tz": tz, "devineName": p.name}
        if p.equip_id in equip_refs:
            e["equipRef"] = equip_refs[p.equip_id]
        space_id = p.space_id if p.space_id in space_refs else equip_space.get(p.equip_id)
        if space_id:
            e["spaceRef"] = space_refs[space_id]
        if p.unit and p.kind == "Number":
            e["unit"] = p.unit
        if p.enum:
            e["enum"] = ",".join(str(x) for x in p.enum)
        if p.his:
            e["his"] = MARKER
        try:
            if point_writable(p.point_type, _address(p.address)):
                e["writable"] = MARKER
        except ValueError:  # an address that does not validate: not described as writable
            pass
        e.update({k: tag_value(v) for k, v in (p.tags or {}).items() if k not in e})
        if live:
            e.update(cur_tags(cache.get(p.id) if cache is not None else None, p.kind, p.unit, devices[p.device_id]))
        nav_parent = str(p.equip_id) if p.equip_id in equip_refs else str(space_id) if space_id else str(site.id)
        _add(m, nav_parent, "point", e)
        m.point_kind[str(p.id)] = p.kind
    return m


def cur_tags(live, kind: str, unit: str | None, device=None) -> dict[str, Any]:
    """curStatus, and curVal only for a Good value (main spec 5.2.1)."""
    if device is not None and not device.enabled:
        return {"curStatus": "disabled"}
    if live is None:
        return {"curStatus": "unknown"}
    out: dict[str, Any] = {"curStatus": live.status.value}
    if live.status == Status.OK and live.value is not None:
        out["curVal"] = his_value(live.value, kind, unit)
    elif live.reason:
        out["curErr"] = live.reason
    return out


def his_value(value: Any, kind: str, unit: str | None) -> Any:
    if kind == "Bool":
        return bool(value)
    if kind == "Str":
        return str(value)
    return Number(float(value), unit)


def _add(m: Model, nav_parent: str | None, kind: str, e: dict) -> None:
    rid = e["id"].val
    m.entities[rid] = e
    m.kinds[rid] = kind
    m.children.setdefault(nav_parent, []).append(rid)


def now_dt(tz: str):
    return to_datetime(datetime.now(UTC), tz)
