"""PostgreSQL + TimescaleDB storage (full mode)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from pydantic import TypeAdapter

from devine_bms.config.model import (
    BacnetAddress,
    BacnetIpConnection,
    BacnetMstpConnection,
    BacnetSettings,
    Bus,
    Kind,
    ModbusAddress,
    ModbusBusConnection,
    ModbusTcpConnection,
    PollClass,
)
from devine_bms.db import models as db
from devine_bms.runtime_model import (
    RuntimeBus,
    RuntimeConfig,
    RuntimeDevice,
    RuntimePoint,
    SiteEquip,
    SiteSpace,
)

_BUS = TypeAdapter(Bus)
_CONNECTIONS = {
    "modbus_tcp": ModbusTcpConnection,
    "modbus": ModbusBusConnection,
    "bacnet_ip": BacnetIpConnection,
    "bacnet_mstp": BacnetMstpConnection,
}
from devine_bms.values import HisRow, Status


class PostgresStorage:
    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self._sessionmaker = sessionmaker

    async def load_runtime_config(self, site_name: str) -> RuntimeConfig:
        async with self._sessionmaker() as session:
            site = await session.scalar(select(db.Site).where(db.Site.name == site_name))
            if site is None:
                raise LookupError(f"site '{site_name}' is not in the database; sync the config file first")
            buses = (await session.scalars(
                select(db.Bus).where(db.Bus.site_id == site.id, db.Bus.enabled.is_(True))
            )).all()
            bus_ids = {b.id for b in buses}
            devices = [
                d for d in (await session.scalars(
                    select(db.Device).where(db.Device.site_id == site.id, db.Device.enabled.is_(True))
                )).all()
                if d.bus_id is None or d.bus_id in bus_ids  # devices on a disabled bus are not polled
            ]
            device_ids = {d.id for d in devices}
            points = (await session.scalars(
                select(db.Point).where(db.Point.site_id == site.id, db.Point.enabled.is_(True)).order_by(db.Point.name)
            )).all()
            spaces = (await session.scalars(
                select(db.Space).where(db.Space.site_id == site.id).order_by(db.Space.name)
            )).all()
            equips = (await session.scalars(
                select(db.Equip).where(db.Equip.site_id == site.id).order_by(db.Equip.name)
            )).all()
        return RuntimeConfig(
            site_id=site.id,
            site_tz=site.tz,
            site_name=site.name,
            bacnet=BacnetSettings.model_validate(site.settings["bacnet"]) if site.settings.get("bacnet") else None,
            site_dis=site.dis,
            spaces=tuple(SiteSpace(s.id, s.name, s.dis, dict(s.tags)) for s in spaces),
            equips=tuple(SiteEquip(e.id, e.name, e.dis, e.space_id, e.parent_equip_id, dict(e.tags)) for e in equips),
            buses=tuple(
                RuntimeBus(
                    b.id,
                    _BUS.validate_python(
                        {**b.settings, "name": b.name, "dis": b.dis, "type": b.bus_type, "enabled": b.enabled}
                    ),
                )
                for b in buses
            ),
            devices=tuple(
                RuntimeDevice(
                    id=d.id, name=d.name, protocol=d.protocol,
                    connection=_CONNECTIONS[d.protocol].model_validate(d.connection),
                    enabled=d.enabled, bus_id=d.bus_id,
                )
                for d in devices
            ),
            points=tuple(
                RuntimePoint(
                    id=p.id, name=p.name, device_id=p.device_id, kind=Kind(p.kind), unit=p.unit,
                    enum=tuple(p.enum) if p.enum else None,
                    address=(BacnetAddress if "instance" in p.address else ModbusAddress).model_validate(p.address),
                    poll_class=PollClass(p.poll_class),
                    stale_after_s=p.stale_after_s, his=p.his, dis=p.dis, point_type=p.point_type,
                    equip_id=p.equip_id, space_id=p.space_id, tags=dict(p.tags),
                )
                for p in points
                if p.device_id in device_ids
            ),
        )

    async def write_history(self, rows: Sequence[HisRow]) -> None:
        if not rows:
            return
        values = [
            {"point_id": r.point_id, "ts": r.ts, "num": r.num, "str": r.str_, "status": r.status.value,
             "reason": r.reason}
            for r in rows
        ]
        statement = insert(db.PointHis.__table__).values(values).on_conflict_do_nothing()
        async with self._sessionmaker() as session, session.begin():
            await session.execute(statement)

    async def read_history(self, point_id: uuid.UUID, start: datetime, end: datetime) -> list[HisRow]:
        async with self._sessionmaker() as session:
            result = await session.scalars(
                select(db.PointHis)
                .where(db.PointHis.point_id == point_id, db.PointHis.ts >= start, db.PointHis.ts < end)
                .order_by(db.PointHis.ts)
            )
            return [
                HisRow(point_id=r.point_id, ts=r.ts, num=r.num, str_=r.str_, status=Status(r.status), reason=r.reason)
                for r in result
            ]
