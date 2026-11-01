"""Database tables (milestone 2 spec section 4).

IDs are UUIDs and serve as the Haystack `id` (main spec 5.2). Times are UTC.
Tags are Haystack tags in a jsonb column: markers are stored as true.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _site_fk() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("site.id", ondelete="CASCADE"), nullable=False, index=True)


def _tags() -> Mapped[dict]:
    return mapped_column(JSONB, nullable=False, server_default="{}")


def _updated() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class Site(Base):
    __tablename__ = "site"
    id: Mapped[uuid.UUID] = _id()
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    dis: Mapped[str] = mapped_column(Text, nullable=False)
    tz: Mapped[str] = mapped_column(String(64), nullable=False, server_default="Sydney")
    geo_lat: Mapped[float | None] = mapped_column(Double)
    geo_lng: Mapped[float | None] = mapped_column(Double)
    area_m2: Mapped[float | None] = mapped_column(Double)
    tags: Mapped[dict] = _tags()
    updated_at: Mapped[datetime] = _updated()


class Space(Base):
    """A room or section (Haystack `space`)."""

    __tablename__ = "space"
    __table_args__ = (UniqueConstraint("site_id", "name"),)
    id: Mapped[uuid.UUID] = _id()
    site_id: Mapped[uuid.UUID] = _site_fk()
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    dis: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[dict] = _tags()
    updated_at: Mapped[datetime] = _updated()


class Equip(Base):
    __tablename__ = "equip"
    __table_args__ = (UniqueConstraint("site_id", "name"),)
    id: Mapped[uuid.UUID] = _id()
    site_id: Mapped[uuid.UUID] = _site_fk()
    space_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("space.id", ondelete="SET NULL"))
    # The "part of" link (Haystack equipRef on the child equipment).
    parent_equip_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("equip.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    dis: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[dict] = _tags()
    updated_at: Mapped[datetime] = _updated()


class EquipFeed(Base):
    """A "feeds" link (main spec 5.2): equipment feeds equipment or a space."""

    __tablename__ = "equip_feed"
    __table_args__ = (
        CheckConstraint("(to_equip_id IS NULL) <> (to_space_id IS NULL)", name="feed_one_target"),
        CheckConstraint(
            "medium IN ('air', 'chilledWater', 'hotWater', 'condenserWater', 'elec', 'refrig')",
            name="feed_medium",
        ),
    )
    id: Mapped[uuid.UUID] = _id()
    site_id: Mapped[uuid.UUID] = _site_fk()
    from_equip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("equip.id", ondelete="CASCADE"), nullable=False)
    to_equip_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("equip.id", ondelete="CASCADE"))
    to_space_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("space.id", ondelete="CASCADE"))
    medium: Mapped[str] = mapped_column(String(32), nullable=False)


class Device(Base):
    __tablename__ = "device"
    __table_args__ = (UniqueConstraint("site_id", "name"),)
    id: Mapped[uuid.UUID] = _id()
    site_id: Mapped[uuid.UUID] = _site_fk()
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    dis: Mapped[str] = mapped_column(Text, nullable=False)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False)
    connection: Mapped[dict] = mapped_column(JSONB, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    updated_at: Mapped[datetime] = _updated()


class Point(Base):
    __tablename__ = "point"
    __table_args__ = (
        UniqueConstraint("site_id", "name"),
        CheckConstraint("equip_id IS NOT NULL OR space_id IS NOT NULL", name="point_has_owner"),
        CheckConstraint("point_type IN ('sensor', 'sp', 'cmd')", name="point_type"),
        CheckConstraint("kind IN ('Number', 'Bool', 'Str')", name="point_kind"),
        CheckConstraint("poll_class IN ('fast', 'normal', 'slow')", name="point_poll_class"),
    )
    id: Mapped[uuid.UUID] = _id()
    site_id: Mapped[uuid.UUID] = _site_fk()
    equip_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("equip.id", ondelete="SET NULL"), index=True)
    space_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("space.id", ondelete="SET NULL"), index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    dis: Mapped[str] = mapped_column(Text, nullable=False)
    point_type: Mapped[str] = mapped_column(String(8), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(32))
    enum: Mapped[list | None] = mapped_column(JSONB)
    tags: Mapped[dict] = _tags()
    address: Mapped[dict] = mapped_column(JSONB, nullable=False)
    poll_class: Mapped[str] = mapped_column(String(8), nullable=False, server_default="normal")
    stale_after_s: Mapped[float | None] = mapped_column(Double)
    his: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # False when the point is no longer in the config file. History is kept.
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    updated_at: Mapped[datetime] = _updated()


class PointAlias(Base):
    """Old names kept after a rename, so old CSV and logic JSON still resolve (main spec 5.2)."""

    __tablename__ = "point_alias"
    __table_args__ = (UniqueConstraint("site_id", "name"),)
    id: Mapped[uuid.UUID] = _id()
    site_id: Mapped[uuid.UUID] = _site_fk()
    point_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("point.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PointHis(Base):
    """History. A TimescaleDB hypertable on `ts` when TimescaleDB is installed."""

    __tablename__ = "point_his"
    __table_args__ = (
        CheckConstraint("status IN ('ok', 'stale', 'fault', 'down', 'disabled')", name="his_status"),
    )
    point_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("point.id", ondelete="CASCADE"), primary_key=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    num: Mapped[float | None] = mapped_column(Double)
    str_: Mapped[str | None] = mapped_column("str", Text)
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
