"""Sync a checked site configuration into the database (milestone 2 spec section 5).

Entities are matched by `name` and created or updated. Nothing is deleted:
points and devices missing from the file are disabled (their history is kept),
and spaces and equipment missing from the file are left as they are and listed
in the report. Feeds are replaced, since they are only links.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from devine_bms.config import model as cfg
from devine_bms.db import models as db


@dataclass
class SyncReport:
    site_id: uuid.UUID | None = None
    created: dict[str, int] = field(default_factory=dict)
    updated: dict[str, int] = field(default_factory=dict)
    disabled: dict[str, list[str]] = field(default_factory=dict)
    not_in_file: dict[str, list[str]] = field(default_factory=dict)

    def _count(self, bucket: dict[str, int], kind: str) -> None:
        bucket[kind] = bucket.get(kind, 0) + 1


async def sync_config(session: AsyncSession, config: cfg.SiteConfig) -> SyncReport:
    """Apply `config` to the database in the caller's transaction."""
    report = SyncReport()

    site = await session.scalar(select(db.Site).where(db.Site.name == config.site.name))
    site_values = dict(
        dis=config.site.dis,
        tz=config.site.tz,
        geo_lat=config.site.geo_coord.lat if config.site.geo_coord else None,
        geo_lng=config.site.geo_coord.lng if config.site.geo_coord else None,
        area_m2=config.site.area_m2,
        tags=dict(config.site.tags),
        settings={"bacnet": config.bacnet.model_dump(mode="json", by_alias=True)} if config.bacnet else {},
    )
    site = _upsert(session, report, "site", site, db.Site, name=config.site.name, **site_values)
    await session.flush()
    report.site_id = site.id

    spaces = await _by_name(session, db.Space, site.id)
    for item in config.spaces:
        spaces[item.name] = _upsert(session, report, "space", spaces.get(item.name), db.Space,
                                    site_id=site.id, name=item.name, dis=item.dis, tags=dict(item.tags))
    await session.flush()

    equips = await _by_name(session, db.Equip, site.id)
    for item in config.equips:
        equips[item.name] = _upsert(
            session, report, "equip", equips.get(item.name), db.Equip,
            site_id=site.id, name=item.name, dis=item.dis, tags=dict(item.tags),
            space_id=spaces[item.space].id if item.space else None,
        )
    await session.flush()
    for item in config.equips:  # parents in a second pass, so order in the file does not matter
        equips[item.name].parent_equip_id = equips[item.parent].id if item.parent else None

    await session.execute(delete(db.EquipFeed).where(db.EquipFeed.site_id == site.id))
    for item in config.feeds:
        session.add(db.EquipFeed(
            site_id=site.id,
            from_equip_id=equips[item.from_].id,
            to_equip_id=equips[item.to].id if item.to in equips else None,
            to_space_id=spaces[item.to].id if item.to not in equips else None,
            medium=item.medium.value,
        ))

    buses = await _by_name(session, db.Bus, site.id)
    for item in config.buses:
        settings = item.model_dump(mode="json", by_alias=True, exclude={"name", "dis", "type", "enabled"})
        buses[item.name] = _upsert(
            session, report, "bus", buses.get(item.name), db.Bus,
            site_id=site.id, name=item.name, dis=item.dis, bus_type=item.type, settings=settings,
            enabled=item.enabled,
        )
    await session.flush()

    devices = await _by_name(session, db.Device, site.id)
    for item in config.devices:
        # The bus a device is on: a Modbus line, an MS/TP trunk or an MQTT broker.
        bus_name = next((getattr(item.connection, f) for f in ("bus", "trunk", "broker")
                         if getattr(item.connection, f, None)), None)
        devices[item.name] = _upsert(
            session, report, "device", devices.get(item.name), db.Device,
            site_id=site.id, name=item.name, dis=item.dis, protocol=item.protocol,
            connection=item.connection.model_dump(mode="json", by_alias=True), enabled=item.enabled,
            bus_id=buses[bus_name].id if bus_name else None,
        )
    await session.flush()

    points = await _by_name(session, db.Point, site.id)
    for item in config.points:
        points[item.name] = _upsert(
            session, report, "point", points.get(item.name), db.Point,
            site_id=site.id, name=item.name, dis=item.dis,
            equip_id=equips[item.equip].id if item.equip else None,
            space_id=spaces[item.space].id if item.space else None,
            device_id=devices[item.device].id,
            point_type=item.type.value, kind=item.kind.value, unit=item.unit, enum=item.enum,
            tags=dict(item.tags), address=item.address.model_dump(mode="json", by_alias=True),
            poll_class=item.poll_class.value, stale_after_s=item.stale_after_s, his=item.his, enabled=True,
        )

    in_file = {
        "bus": {b.name for b in config.buses},
        "space": {s.name for s in config.spaces},
        "equip": {e.name for e in config.equips},
        "device": {d.name for d in config.devices},
        "point": {p.name for p in config.points},
    }
    for name, row in points.items():
        if name not in in_file["point"] and row.enabled:
            row.enabled = False
            report.disabled.setdefault("point", []).append(name)
    for name, row in devices.items():
        if name not in in_file["device"] and row.enabled:
            row.enabled = False
            report.disabled.setdefault("device", []).append(name)
    for name, row in buses.items():
        if name not in in_file["bus"] and row.enabled:
            row.enabled = False
            report.disabled.setdefault("bus", []).append(name)
    for kind, rows in (("space", spaces), ("equip", equips)):
        missing = sorted(n for n in rows if n not in in_file[kind])
        if missing:
            report.not_in_file[kind] = missing

    await session.flush()
    return report


async def _by_name(session: AsyncSession, table, site_id: uuid.UUID) -> dict:
    rows = await session.scalars(select(table).where(table.site_id == site_id))
    return {row.name: row for row in rows}


def _upsert(session: AsyncSession, report: SyncReport, label: str, row, table, /, **values):
    """Create `row` or update its changed columns. `label` is the report bucket."""
    if row is None:
        row = table(**values)
        session.add(row)
        report._count(report.created, label)
        return row
    changed = False
    for key, value in values.items():
        if getattr(row, key) != value:
            setattr(row, key, value)
            changed = True
    if changed:
        report._count(report.updated, label)
    return row
