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
    # Site-wide settings from the config file, for example the BMS's own BACnet identity.
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    # Login rules, kiosk addresses and allowed networks (milestone 6A). Not part of the config
    # file, so importing a setup never changes them.
    security: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    # Alarm delivery: channels per level, SMTP, ClickSend, ntfy and the recipient list (6B).
    # Personal data, kept on the site server only and not in the config file (main spec 5.5.1).
    alarm_settings: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    # Override times offered to operators (7A): {"choicesH": [1, 2, 8], "defaultH": 2}.
    command_settings: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
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
    # False once removed from the setup; kept so old points keep their room (milestone 5A).
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # Made from a room template: {id, version, bindings, parameters} (main spec 5.1.2).
    template: Mapped[dict | None] = mapped_column(JSONB)
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
    # False once removed from the setup; kept so old points keep their equipment (milestone 5A).
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # Made from an equipment template: {id, version, bindings, parameters} (main spec 5.1.2).
    template: Mapped[dict | None] = mapped_column(JSONB)
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


class Bus(Base):
    """An RS485 line or a serial-to-Ethernet gateway that several devices share (main spec 5.1)."""

    __tablename__ = "bus"
    __table_args__ = (
        UniqueConstraint("site_id", "name"),
        CheckConstraint(
            "bus_type IN ('modbus_rtu', 'modbus_rtu_over_tcp', 'modbus_tcp_gateway', 'bacnet_mstp', "
            "'mqtt_broker')",
            name="bus_type",
        ),
    )
    id: Mapped[uuid.UUID] = _id()
    site_id: Mapped[uuid.UUID] = _site_fk()
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    dis: Mapped[str] = mapped_column(Text, nullable=False)
    bus_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # Port settings or gateway address, timeout and retries, as in the config file.
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    updated_at: Mapped[datetime] = _updated()


class Device(Base):
    __tablename__ = "device"
    __table_args__ = (UniqueConstraint("site_id", "name"),)
    id: Mapped[uuid.UUID] = _id()
    site_id: Mapped[uuid.UUID] = _site_fk()
    # Set for devices on a bus; their connection holds the unit ID and block size.
    bus_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bus.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    dis: Mapped[str] = mapped_column(Text, nullable=False)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False)
    connection: Mapped[dict] = mapped_column(JSONB, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    offline_alarm: Mapped[dict | None] = mapped_column(JSONB)  # milestone 6B
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
    alarms: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")  # milestone 6B
    write_limits: Mapped[dict | None] = mapped_column(JSONB)  # milestone 7A
    occupancy: Mapped[dict | None] = mapped_column(JSONB)  # milestone 7B: {occupied, unoccupied}
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


class AuditEntry(Base):
    """One entry in the audit log (main spec 5.6): setup changes, scans, logins and user changes.
    Milestone 5 called it the setup change log. Kept for 1 year."""

    __tablename__ = "audit_log"
    id: Mapped[uuid.UUID] = _id()
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("site.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(),
                                         index=True)
    client: Mapped[str] = mapped_column(String(64), nullable=False)  # the client address
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"), index=True)
    username: Mapped[str | None] = mapped_column(String(64))  # kept when the account is removed
    action: Mapped[str] = mapped_column(String(32), nullable=False)  # import, put, delete, login, ...
    kind: Mapped[str | None] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(String(100))
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    before: Mapped[dict | list | None] = mapped_column(JSONB)
    after: Mapped[dict | list | None] = mapped_column(JSONB)


SetupChange = AuditEntry  # the milestone 5 name


class User(Base):
    """A person's account on this site (main spec 7). Roles: viewer, operator, admin."""

    __tablename__ = "app_user"
    __table_args__ = (UniqueConstraint("site_id", "username"),
                      CheckConstraint("role IN ('viewer', 'operator', 'admin')", name="ck_user_role"))
    id: Mapped[uuid.UUID] = _id()
    site_id: Mapped[uuid.UUID] = _site_fk()
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    dis: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    platform: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    totp_secret: Mapped[str | None] = mapped_column(String(64))  # set once 2FA is confirmed
    totp_pending: Mapped[str | None] = mapped_column(String(64))  # during 2FA set-up
    pin_hash: Mapped[str | None] = mapped_column(Text)  # HMI PIN (milestone 7A), separate from the password
    failed_logins: Mapped[int] = mapped_column(nullable=False, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = _updated()


class UserSession(Base):
    """A signed-in browser. Only a hash of the cookie's token is kept."""

    __tablename__ = "user_session"
    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False,
                                               index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    client: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False, server_default="web")  # web, or pin (HMI)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DeviceTemplateRow(Base):
    """A site's own device template, one row per version (main spec 5.1, milestone 5A).

    Built-in templates ship as files with the software and are not stored here.
    """

    __tablename__ = "device_template"
    __table_args__ = (UniqueConstraint("site_id", "template_id", "version"),)
    id: Mapped[uuid.UUID] = _id()
    site_id: Mapped[uuid.UUID] = _site_fk()
    template_id: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Alarm(Base):
    """One occurrence of an alarm (main spec 5.5): raised, maybe returned to normal, acknowledged.
    It is closed (off the active list) once it is both acknowledged and back to normal."""

    __tablename__ = "alarm"
    id: Mapped[uuid.UUID] = _id()
    site_id: Mapped[uuid.UUID] = _site_fk()
    key: Mapped[str] = mapped_column(String(200), nullable=False, index=True)  # which definition
    point_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("point.id", ondelete="SET NULL"), index=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("device.id", ondelete="SET NULL"), index=True)
    source: Mapped[str] = mapped_column(String(100), nullable=False)  # point or device name when raised
    source_dis: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)  # high, low, state, stale, offline
    level: Mapped[str] = mapped_column(String(16), nullable=False)  # info, warning, critical
    message: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")  # value, limit, unit
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    closed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false", index=True)
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acked_by: Mapped[str | None] = mapped_column(String(64))


class AlarmEvent(Base):
    """The history of an alarm: raised, returned to normal, acknowledged, comments."""

    __tablename__ = "alarm_event"
    id: Mapped[uuid.UUID] = _id()
    alarm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alarm.id", ondelete="CASCADE"), nullable=False,
                                                index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # raised, returned, acked, comment
    username: Mapped[str | None] = mapped_column(String(64))
    text: Mapped[str | None] = mapped_column(Text)


class AlarmDelivery(Base):
    """One notification to one person on one channel (main spec 5.5.1 delivery log)."""

    __tablename__ = "alarm_delivery"
    id: Mapped[uuid.UUID] = _id()
    site_id: Mapped[uuid.UUID] = _site_fk()
    alarm_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("alarm.id", ondelete="SET NULL"), index=True)
    recipient: Mapped[str] = mapped_column(String(100), nullable=False)
    channel: Mapped[str] = mapped_column(String(8), nullable=False)  # email, sms, ntfy
    address: Mapped[str] = mapped_column(String(200), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, index=True)  # pending, held, sent, failed
    attempts: Mapped[int] = mapped_column(nullable=False, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hold_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # quiet hours (Warning only)
    error: Mapped[str | None] = mapped_column(Text)
    test: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(),
                                                 index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PointOverride(Base):
    """A hand-mode override in force (main spec 5.6): it ends at `ends_at` and the point returns
    to auto. Stored so it survives a restart."""

    __tablename__ = "point_override"
    point_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("point.id", ondelete="CASCADE"), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)  # {"v": value}
    previous: Mapped[dict] = mapped_column(JSONB, nullable=False)  # {"v": value before, or null}
    set_by: Mapped[str] = mapped_column(String(64), nullable=False)
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class HolidayCalendar(Base):
    """Dates when the site is closed, shared by schedules (milestone 7B): each entry is
    {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD" or null, "dis": "..."}; those days are Unoccupied."""

    __tablename__ = "holiday_calendar"
    __table_args__ = (UniqueConstraint("site_id", "name"),)
    id: Mapped[uuid.UUID] = _id()
    site_id: Mapped[uuid.UUID] = _site_fk()
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    dis: Mapped[str] = mapped_column(Text, nullable=False)
    dates: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    updated_at: Mapped[datetime] = _updated()


class Schedule(Base):
    """An occupancy schedule (main spec 5.4, milestone 7B): a weekly pattern of Occupied periods,
    holiday calendars, one-off exceptions, the equipment it applies to, and an optional copy in
    a BACnet controller."""

    __tablename__ = "schedule"
    __table_args__ = (UniqueConstraint("site_id", "name"),)
    id: Mapped[uuid.UUID] = _id()
    site_id: Mapped[uuid.UUID] = _site_fk()
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    dis: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    weekly: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")  # {"mon": [{"start","end"}]}
    calendars: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")  # calendar names
    exceptions: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    equips: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")  # equipment names
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")  # or every equipment with a tag
    controller: Mapped[dict | None] = mapped_column(JSONB)  # the BACnet Schedule object it is copied to
    controller_state: Mapped[dict | None] = mapped_column(JSONB)  # last push and read-back
    updated_at: Mapped[datetime] = _updated()


class LogicDiagram(Base):
    """A control logic diagram (main spec 5.3.1, milestone 10A): its draft (saved on every
    change, may be incomplete) and which published version runs."""

    __tablename__ = "logic_diagram"
    __table_args__ = (UniqueConstraint("site_id", "name"),)
    id: Mapped[uuid.UUID] = _id()
    site_id: Mapped[uuid.UUID] = _site_fk()
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    dis: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    draft: Mapped[dict] = mapped_column(JSONB, nullable=False)  # devine-bms-logic/1
    draft_by: Mapped[str | None] = mapped_column(String(100))
    draft_at: Mapped[datetime] = _updated()
    published_version: Mapped[int | None] = mapped_column()


class LogicVersion(Base):
    """A published version of a diagram; earlier ones can be restored into the draft."""

    __tablename__ = "logic_version"
    __table_args__ = (UniqueConstraint("diagram_id", "version"),)
    id: Mapped[uuid.UUID] = _id()
    diagram_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("logic_diagram.id", ondelete="CASCADE"),
                                                  nullable=False, index=True)
    version: Mapped[int] = mapped_column(nullable=False)
    body: Mapped[dict] = mapped_column(JSONB, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    published_by: Mapped[str | None] = mapped_column(String(100))
    published_at: Mapped[datetime] = _updated()


class LogicVariable(Base):
    """A logic variable's value, kept through a restart (main spec 5.3.1 Variables). Scope `site`
    (owner empty; its definition is here too) or `diagram` (owner = diagram name; defined in the
    diagram)."""

    __tablename__ = "logic_variable"
    __table_args__ = (UniqueConstraint("site_id", "scope", "owner", "name"),)
    id: Mapped[uuid.UUID] = _id()
    site_id: Mapped[uuid.UUID] = _site_fk()
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    owner: Mapped[str] = mapped_column(String(100), nullable=False, server_default="")
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    dis: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(8), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[dict | None] = mapped_column(JSONB)  # {"v": value}
    min: Mapped[float | None] = mapped_column(Double)
    max: Mapped[float | None] = mapped_column(Double)
    updated_by: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime] = _updated()


class LogicInstance(Base):
    """A template logic instance's switch (milestone 10C): instances run unless switched off."""

    __tablename__ = "logic_instance"
    __table_args__ = (UniqueConstraint("site_id", "name"),)
    id: Mapped[uuid.UUID] = _id()
    site_id: Mapped[uuid.UUID] = _site_fk()
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # <instance>__tpl
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    updated_at: Mapped[datetime] = _updated()
