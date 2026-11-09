"""Export the site setup from the database as a `devine-bms-config/0` document (milestone 5A).

The database is the master copy (milestone 5 decision 1); this is the export format,
and the starting point of every setup change. Disabled points, devices and buses
(removed earlier, kept for their history) are not exported.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from devine_bms.config.model import SCHEMA_ID, SiteConfig
from devine_bms.db import models as db


class NoSite(LookupError):
    pass


async def export_dict(session: AsyncSession, site_name: str | None = None) -> dict:
    """The setup as a plain dict in the config file format (camelCase keys)."""
    query = select(db.Site)
    if site_name:
        query = query.where(db.Site.name == site_name)
    sites = (await session.scalars(query.order_by(db.Site.name))).all()
    if not sites:
        raise NoSite(f"no site '{site_name}' in the database" if site_name else "no site in the database")
    if len(sites) > 1:
        raise NoSite("more than one site in the database; name the site to export")
    site = sites[0]

    def rows(table, *conditions):
        return session.scalars(select(table).where(table.site_id == site.id, *conditions).order_by(table.name))

    spaces = (await rows(db.Space, db.Space.enabled.is_(True))).all()
    equips = (await rows(db.Equip, db.Equip.enabled.is_(True))).all()
    buses = (await rows(db.Bus, db.Bus.enabled.is_(True))).all()
    devices = (await rows(db.Device, db.Device.enabled.is_(True))).all()
    points = (await rows(db.Point, db.Point.enabled.is_(True))).all()
    feeds = (await session.scalars(select(db.EquipFeed).where(db.EquipFeed.site_id == site.id))).all()

    space_names = {s.id: s.name for s in spaces}
    equip_names = {e.id: e.name for e in equips}
    device_names = {d.id: d.name for d in devices}

    data: dict = {
        "schema": SCHEMA_ID,
        "site": _drop_none({
            "name": site.name, "dis": site.dis, "tz": site.tz,
            "geoCoord": {"lat": site.geo_lat, "lng": site.geo_lng} if site.geo_lat is not None else None,
            "areaM2": site.area_m2, "tags": dict(site.tags),
        }),
        "spaces": [_drop_none({"name": s.name, "dis": s.dis, "tags": dict(s.tags), "template": s.template})
                   for s in spaces],
        "equips": [
            _drop_none({"name": e.name, "dis": e.dis, "space": space_names.get(e.space_id),
                        "parent": equip_names.get(e.parent_equip_id), "tags": dict(e.tags),
                        "template": e.template, "service": e.service})
            for e in equips
        ],
        "feeds": sorted(
            ({"from": equip_names[f.from_equip_id],
              "to": equip_names.get(f.to_equip_id) or space_names.get(f.to_space_id),
              "medium": f.medium} for f in feeds),
            key=lambda f: (f["from"], f["to"], f["medium"]),
        ),
        "buses": [{"name": b.name, "dis": b.dis, "type": b.bus_type, "enabled": b.enabled, **b.settings}
                  for b in buses],
        "devices": [
            _drop_none({"name": d.name, "dis": d.dis, "protocol": d.protocol, "connection": dict(d.connection),
                        "enabled": d.enabled, "offlineAlarm": d.offline_alarm})
            for d in devices
        ],
        "points": [
            _drop_none({
                "name": p.name, "dis": p.dis, "equip": equip_names.get(p.equip_id),
                "space": space_names.get(p.space_id), "device": device_names.get(p.device_id),
                "type": p.point_type, "kind": p.kind, "unit": p.unit, "enum": p.enum, "tags": dict(p.tags),
                "address": dict(p.address), "pollClass": p.poll_class, "staleAfterS": p.stale_after_s,
                "his": p.his, "alarms": list(p.alarms) or None, "writeLimits": p.write_limits, "occupancy": p.occupancy,
            })
            for p in points
            if p.device_id in device_names  # points of a removed device are not part of the setup
        ],
    }
    if site.settings.get("bacnet"):
        data["bacnet"] = dict(site.settings["bacnet"])
    if site.settings.get("equipTemplates"):
        data["equipTemplates"] = list(site.settings["equipTemplates"])
    if site.settings.get("logicSnippets"):
        data["logicSnippets"] = list(site.settings["logicSnippets"])
    if site.settings.get("symbols"):
        data["symbols"] = list(site.settings["symbols"])
    if site.settings.get("graphics"):
        data["graphics"] = list(site.settings["graphics"])
    # Maintenance counters and service marks (main spec 5.10): the BMS's own counts.
    counted = (await session.execute(
        select(db.EquipCounter, db.Equip.name).join(db.Equip, db.Equip.id == db.EquipCounter.equip_id)
        .where(db.Equip.site_id == site.id).order_by(db.Equip.name))).all()
    if counted:
        data["counters"] = [_drop_none({
            "equip": name, "runHours": round(c.run_s / 3600.0, 4), "starts": int(c.starts),
            "serviceHoursMark": round(c.service_hours_mark, 4), "serviceStartsMark": int(c.service_starts_mark),
            "servicedAt": c.serviced_at.isoformat() if c.serviced_at else None, "servicedBy": c.serviced_by})
            for c, name in counted]
    return data


async def export_config(session: AsyncSession, site_name: str | None = None) -> SiteConfig:
    return SiteConfig.model_validate(await export_dict(session, site_name))


def _drop_none(values: dict) -> dict:
    return {k: v for k, v in values.items() if v is not None}
