"""Alarms over HTTP (main spec 5.5 and 5.5.1; milestone 6B).

Viewing alarms, their history and the delivery log: any signed-in role. Acknowledging and
commenting: operator and admin (main spec 7). Delivery settings, recipients and test
messages: admin. Acknowledgements, comments and settings changes go to the audit log.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select

from devine_bms.alarms.delivery import Delivery, SettingsError, mask
from devine_bms.alarms.engine import LEVEL_ORDER, AlarmEngine, alarm_json
from devine_bms.api.auth_api import actor_of, require
from devine_bms.auth.service import Actor, AuthService
from devine_bms.db import models as db


def build_alarms_router(get_engine: Callable[[], AlarmEngine | None], get_delivery: Callable[[], Delivery | None],
                        get_auth: Callable[[], AuthService | None]) -> APIRouter:
    router = APIRouter()

    def engine() -> AlarmEngine:
        e = get_engine()
        if e is None:
            raise HTTPException(503, "alarms need the database")
        return e

    def delivery() -> Delivery:
        d = get_delivery()
        if d is None:
            raise HTTPException(503, "alarms need the database")
        return d

    async def audit(actor: Actor, action: str, summary: str, name: str | None = None, after: Any = None) -> None:
        auth = get_auth()
        if auth is not None:
            await auth.record(actor, action, summary, kind="alarm", name=name, after=after)

    def operator(request: Request) -> Actor:
        actor = require("operator")(request)
        if actor.username is None:
            raise HTTPException(403, "Acknowledging needs a person's login.")
        return actor

    # ---------- reading ----------

    @router.get("/api/alarms")
    async def alarms(request: Request, state: str = "open", level: str | None = None, q: str | None = None,
                     start: datetime | None = None, end: datetime | None = None,
                     limit: int = Query(200, ge=1, le=5000), e: AlarmEngine = Depends(engine)) -> Any:
        require("viewer")(request)
        async with e.sessions() as session:
            query = select(db.Alarm).where(db.Alarm.site_id == e._site_id)
            if state == "open":
                query = query.where(db.Alarm.closed.is_(False))
            elif state == "closed":
                query = query.where(db.Alarm.closed.is_(True))
            if level:
                query = query.where(db.Alarm.level == level)
            if q:
                like = f"%{q}%"
                query = query.where(or_(db.Alarm.message.ilike(like), db.Alarm.source.ilike(like),
                                        db.Alarm.source_dis.ilike(like)))
            if start:
                query = query.where(db.Alarm.raised_at >= start)
            if end:
                query = query.where(db.Alarm.raised_at < end)
            rows = (await session.scalars(query.order_by(db.Alarm.raised_at.desc()).limit(limit))).all()
        out = [alarm_json(r) for r in rows]
        if state == "open":  # unacknowledged and active first, then by level
            out.sort(key=lambda a: (a["ackedAt"] is not None, not a["active"], LEVEL_ORDER[a["level"]]))
        return out

    @router.get("/api/alarms/summary")
    async def summary(request: Request, e: AlarmEngine = Depends(engine)) -> Any:
        require("viewer")(request)
        async with e.sessions() as session:
            rows = (await session.execute(
                select(db.Alarm.level, func.count()).where(db.Alarm.site_id == e._site_id, db.Alarm.closed.is_(False),
                                                          db.Alarm.acked_at.is_(None))
                .group_by(db.Alarm.level))).all()
            active = await session.scalar(select(func.count()).select_from(db.Alarm).where(
                db.Alarm.site_id == e._site_id, db.Alarm.active.is_(True)))
        counts = {lvl: 0 for lvl in LEVEL_ORDER}
        counts.update({lvl: n for lvl, n in rows})
        return {"unacked": counts, "active": active or 0}

    def delivery_json(d: db.AlarmDelivery, admin: bool) -> dict:
        return {"id": str(d.id), "alarmId": str(d.alarm_id) if d.alarm_id else None, "recipient": d.recipient,
                "channel": d.channel, "address": d.address if admin else mask(d.channel, d.address),
                "level": d.level, "status": d.status, "attempts": d.attempts, "error": d.error, "test": d.test,
                "createdAt": d.created_at.isoformat(), "sentAt": d.sent_at.isoformat() if d.sent_at else None,
                "holdUntil": d.hold_until.isoformat() if d.hold_until else None}

    @router.get("/api/alarms/{alarm_id}")
    async def one(alarm_id: uuid.UUID, request: Request, e: AlarmEngine = Depends(engine)) -> Any:
        actor = require("viewer")(request)
        async with e.sessions() as session:
            row = await session.scalar(select(db.Alarm).where(db.Alarm.id == alarm_id, db.Alarm.site_id == e._site_id))
            if row is None:
                raise HTTPException(404, "no such alarm")
            events = (await session.scalars(select(db.AlarmEvent).where(db.AlarmEvent.alarm_id == row.id)
                                            .order_by(db.AlarmEvent.ts))).all()
            sends = (await session.scalars(select(db.AlarmDelivery).where(db.AlarmDelivery.alarm_id == row.id)
                                           .order_by(db.AlarmDelivery.created_at))).all()
        return {**alarm_json(row),
                "events": [{"ts": ev.ts.isoformat(), "kind": ev.kind, "user": ev.username, "text": ev.text}
                           for ev in events],
                "deliveries": [delivery_json(d, actor.can("admin")) for d in sends]}

    @router.get("/api/alarm-deliveries")
    async def deliveries(request: Request, limit: int = Query(200, ge=1, le=5000), status: str | None = None,
                         e: AlarmEngine = Depends(engine)) -> Any:
        actor = require("viewer")(request)
        async with e.sessions() as session:
            query = select(db.AlarmDelivery).where(db.AlarmDelivery.site_id == e._site_id)
            if status:
                query = query.where(db.AlarmDelivery.status == status)
            rows = (await session.scalars(query.order_by(db.AlarmDelivery.created_at.desc()).limit(limit))).all()
        return [delivery_json(d, actor.can("admin")) for d in rows]

    # ---------- acknowledging ----------

    @router.post("/api/alarms/ack")
    async def ack(body: dict = Body(...), actor: Actor = Depends(operator), e: AlarmEngine = Depends(engine)) -> Any:
        try:
            ids = [uuid.UUID(str(i)) for i in body.get("ids", [])]
        except ValueError:
            raise HTTPException(422, "ids must be alarm ids") from None
        if not ids:
            raise HTTPException(422, "give the ids of the alarms to acknowledge")
        comment = str(body.get("comment") or "").strip()[:1000] or None
        done = await e.acknowledge(ids, actor.username, comment)
        for a in done:
            await audit(actor, "alarm-ack", f"acknowledged {a['level']} alarm: {a['message']}" +
                        (f" ({comment})" if comment else ""), a["source"], {"alarm": a["id"]})
        return {"ok": True, "acknowledged": done}

    @router.post("/api/alarms/{alarm_id}/comment")
    async def comment(alarm_id: uuid.UUID, body: dict = Body(...), actor: Actor = Depends(operator),
                      e: AlarmEngine = Depends(engine)) -> Any:
        text = str(body.get("text") or "").strip()[:1000]
        if not text:
            raise HTTPException(422, "write a comment")
        try:
            await e.comment(alarm_id, actor.username, text)
        except KeyError:
            raise HTTPException(404, "no such alarm") from None
        await audit(actor, "alarm-comment", f"commented on an alarm: {text[:100]}", None, {"alarm": str(alarm_id)})
        return {"ok": True}

    # ---------- delivery settings (admin) ----------

    admin = require("admin")

    @router.get("/api/alarm-settings")
    async def get_settings(actor: Actor = Depends(admin), d: Delivery = Depends(delivery)) -> Any:
        from devine_bms.alarms.delivery import check_settings
        s = await d.settings()
        try:
            warnings = check_settings(s)
        except SettingsError as exc:
            warnings = [exc.message]
        return {"settings": s, "warnings": warnings}

    @router.put("/api/alarm-settings")
    async def put_settings(body: dict = Body(...), actor: Actor = Depends(admin), d: Delivery = Depends(delivery)) -> Any:
        before = await d.settings()
        try:
            s, warnings = await d.save_settings(body)
        except SettingsError as exc:
            return JSONResponse({"ok": False, "error": exc.message}, status_code=422)
        names = lambda x: sorted(r["name"] for r in x["recipients"])  # noqa: E731
        await audit(actor, "settings", "changed the alarm delivery settings", "alarm-settings",
                    {"channels": s["channels"], "recipients": names(s), "recipientsBefore": names(before)})
        return {"ok": True, "settings": s, "warnings": warnings}

    @router.post("/api/alarm-settings/test")
    async def test(body: dict = Body(...), actor: Actor = Depends(admin), d: Delivery = Depends(delivery)) -> Any:
        try:
            result = await d.send_test(str(body.get("recipient", "")), str(body.get("channel", "")))
        except SettingsError as exc:
            return JSONResponse({"ok": False, "error": exc.message}, status_code=422)
        await audit(actor, "alarm-test", f"sent a test {body.get('channel')} message: "
                    f"{'delivered' if result['ok'] else 'failed'}", None, None)
        return result

    return router
