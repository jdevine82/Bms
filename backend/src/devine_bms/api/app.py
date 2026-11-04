"""REST and WebSocket API (milestone 2 spec 7).

All times are ISO 8601 in UTC; the web app shows them in the site time zone.
No logins yet (main spec 7 comes later), so the server listens on localhost only
by default.

Run:  uv run uvicorn devine_bms.api.app:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from devine_bms import __version__
from devine_bms.api.history import history_csv, row_json, thin
from devine_bms.api.timezones import iana_zone
from devine_bms.runtime import Runtime
from devine_bms.runtime_model import RuntimeConfig, RuntimePoint
from devine_bms.values import LiveValue, Status

log = logging.getLogger(__name__)

MAX_RANGE = timedelta(days=31)
RuntimeFactory = Callable[[], Awaitable[tuple[Runtime, Callable[[], Awaitable[None]]]]]


async def default_runtime() -> tuple[Runtime, Callable[[], Awaitable[None]]]:
    """Full mode: sync the config file into PostgreSQL, then start the runtime."""
    from devine_bms.config.sync import sync_config
    from devine_bms.config.validate import load_config
    from devine_bms.db.session import make_engine, make_sessionmaker
    from devine_bms.logging_setup import setup_logging
    from devine_bms.settings import get_settings
    from devine_bms.storage.postgres import PostgresStorage

    settings = get_settings()
    setup_logging(settings.log_level)
    config, warnings = load_config(settings.config_path)
    for warning in warnings:
        log.warning("config: %s", warning)
    engine = make_engine(settings.database_url)
    sessions = make_sessionmaker(engine)
    async with sessions() as session, session.begin():
        await sync_config(session, config)
    runtime = Runtime(PostgresStorage(sessions), config.site.name)
    return runtime, engine.dispose


def live_json(live: LiveValue | None) -> dict:
    if live is None:
        return {"status": Status.UNKNOWN.value}
    good = live.status == Status.OK
    return {
        "id": str(live.point_id),
        # A Bad or Stale value is never given as the live value (main spec 5.2.1).
        "value": live.value if good else None,
        "lastValue": live.value,
        "status": live.status.value,
        "reason": live.reason,
        "ts": live.ts.isoformat(),
        "lastGoodTs": live.last_good_ts.isoformat() if live.last_good_ts else None,
    }


def create_app(runtime_factory: RuntimeFactory = default_runtime, web_dist: Path | None = None) -> FastAPI:
    state: dict = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        runtime, cleanup = await runtime_factory()
        await runtime.start()
        state["runtime"] = runtime
        try:
            yield
        finally:
            await runtime.stop()
            await cleanup()

    app = FastAPI(title="Devine BMS", version=__version__, lifespan=lifespan)

    def runtime() -> Runtime:
        return state["runtime"]

    def config() -> RuntimeConfig:
        return runtime().config

    def find_point(point_id: uuid.UUID) -> RuntimePoint:
        for point in config().points:
            if point.id == point_id:
                return point
        raise HTTPException(404, "point not found")

    def point_json(point: RuntimePoint) -> dict:
        cfg = config()
        equip = next((e for e in cfg.equips if e.id == point.equip_id), None)
        space_id = point.space_id or (equip.space_id if equip else None)  # inherits its equipment's room
        space = next((s for s in cfg.spaces if s.id == space_id), None)
        return {
            "id": str(point.id),
            "name": point.name,
            "dis": point.dis,
            "equip": {"id": str(equip.id), "name": equip.name, "dis": equip.dis} if equip else None,
            "space": {"id": str(space.id), "name": space.name, "dis": space.dis} if space else None,
            "type": point.point_type,
            "kind": point.kind.value,
            "unit": point.unit,
            "enum": list(point.enum) if point.enum else None,
            "tags": point.tags,
            "his": point.his,
            "pollClass": point.poll_class.value,
            "cur": live_json(runtime().cache.get(point.id)),
        }

    def time_range(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
        end = end or datetime.now(UTC)
        start = start or end - timedelta(hours=1)
        if start.tzinfo is None or end.tzinfo is None:
            raise HTTPException(422, "start and end need a time zone, for example 2026-11-01T00:00:00Z")
        if end <= start:
            raise HTTPException(422, "end must be after start")
        if end - start > MAX_RANGE:
            raise HTTPException(422, "the range can be at most 31 days")
        return start, end

    @app.get("/api/health")
    async def health() -> dict:
        rt = runtime()
        return {
            "status": "ok",
            "version": __version__,
            "devices": [
                {"name": p.device.name, "online": p.online, "polls": p.polls, "failures": p.failures}
                for p in rt.pollers
            ],
            "connections": [
                {"name": b.name, "where": b.client.where, "shared": b.client.shared, "online": b.online,
                 "devices": [d.device.name for d in b.devices]}
                for b in rt.buses
            ],
            "history": {"written": rt.history.written, "pending": len(rt.history.pending)},
        }

    @app.get("/api/site")
    async def site() -> dict:
        cfg = config()
        return {
            "id": str(cfg.site_id),
            "name": cfg.site_name,
            "dis": cfg.site_dis,
            "tz": cfg.site_tz,
            "ianaTz": iana_zone(cfg.site_tz).key,
            "spaces": [{"id": str(s.id), "name": s.name, "dis": s.dis, "tags": s.tags} for s in cfg.spaces],
            "equips": [
                {"id": str(e.id), "name": e.name, "dis": e.dis, "tags": e.tags,
                 "spaceId": str(e.space_id) if e.space_id else None,
                 "parentId": str(e.parent_id) if e.parent_id else None}
                for e in cfg.equips
            ],
        }

    @app.get("/api/points")
    async def points(
        equip: str | None = None, space: str | None = None, tag: list[str] = Query(default=[])
    ) -> list[dict]:
        result = []
        for point in config().points:
            data = point_json(point)
            if equip and (not data["equip"] or equip not in (data["equip"]["id"], data["equip"]["name"])):
                continue
            if space and (not data["space"] or space not in (data["space"]["id"], data["space"]["name"])):
                continue
            if any(t not in point.tags and t != point.point_type for t in tag):
                continue
            result.append(data)
        return result

    @app.get("/api/points/{point_id}")
    async def one_point(point_id: uuid.UUID) -> dict:
        return point_json(find_point(point_id))

    @app.get("/api/points/{point_id}/history")
    async def history(point_id: uuid.UUID, start: datetime | None = None, end: datetime | None = None) -> dict:
        point = find_point(point_id)
        start, end = time_range(start, end)
        rows = await runtime().storage.read_history(point.id, start, end)
        kept, thinned = thin(rows, start, end)
        return {
            "id": str(point.id), "name": point.name, "unit": point.unit, "kind": point.kind.value,
            "start": start.isoformat(), "end": end.isoformat(),
            "thinned": thinned, "rowCount": len(rows),
            "rows": [row_json(r) for r in kept],
        }

    @app.get("/api/points/{point_id}/history.csv", response_class=PlainTextResponse)
    async def history_as_csv(
        point_id: uuid.UUID, start: datetime | None = None, end: datetime | None = None
    ) -> PlainTextResponse:
        point = find_point(point_id)
        start, end = time_range(start, end)
        rows = await runtime().storage.read_history(point.id, start, end)
        body = history_csv(rows, point.name, point.unit, iana_zone(config().site_tz))
        filename = f"{point.name}_{start:%Y%m%d-%H%M}_{end:%Y%m%d-%H%M}.csv"
        return PlainTextResponse(body, media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.websocket("/api/live")
    async def live(ws: WebSocket) -> None:
        """Send {"subscribe": ["<point id>", ...]} or {"subscribe": "all"}. The server replies
        with a snapshot of current values, then one message per change."""
        await ws.accept()
        rt = runtime()
        queue = rt.cache.subscribe()
        wanted: set[uuid.UUID] = set()
        known = {p.id for p in rt.config.points}

        async def receive() -> None:
            nonlocal wanted
            while True:
                message = await ws.receive_json()
                ids = message.get("subscribe") if isinstance(message, dict) else None
                if ids == "all":
                    wanted = set(known)
                elif isinstance(ids, list):
                    try:
                        wanted = {uuid.UUID(str(i)) for i in ids} & known
                    except ValueError:
                        await ws.send_json({"type": "error", "message": "subscribe needs point ids"})
                        continue
                else:
                    await ws.send_json({"type": "error", "message": 'send {"subscribe": [ids]} or "all"'})
                    continue
                await ws.send_json({"type": "snapshot",
                                    "values": [live_json(rt.cache.get(i)) for i in sorted(wanted, key=str)]})

        async def send() -> None:
            while True:
                change = await queue.get()
                if change.point_id in wanted:
                    await ws.send_json({"type": "value", **live_json(change)})

        tasks = [asyncio.create_task(receive()), asyncio.create_task(send())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                error = task.exception()
                if error and not isinstance(error, WebSocketDisconnect):
                    log.warning("live connection closed: %s", error)
        finally:
            for task in tasks:
                task.cancel()
            rt.cache.unsubscribe(queue)

    if web_dist and web_dist.is_dir():
        index = web_dist / "index.html"

        # The Pi kiosk opens /hmi; the same app picks its HMI layout from the path (milestone 3 spec 4).
        @app.get("/hmi", include_in_schema=False)
        @app.get("/hmi/{rest:path}", include_in_schema=False)
        async def hmi(rest: str = "") -> FileResponse:
            return FileResponse(index, headers={"Cache-Control": "no-cache"})

        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
    return app


def _default_web_dist() -> Path | None:
    from devine_bms.settings import get_settings

    path = get_settings().web_dist
    return Path(path) if path else None


app = create_app(web_dist=_default_web_dist())
