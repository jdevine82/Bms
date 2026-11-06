"""REST and WebSocket API (milestone 2 spec 7).

All times are ISO 8601 in UTC; the web app shows them in the site time zone.
Every route needs a login (milestone 6A, main spec 7) except the health check and the
login pages; see api/auth_api.py.

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
from devine_bms.api.auth_api import COOKIE, build_auth_router, client_ip, install_guard
from devine_bms.api.history import history_csv, row_json, thin
from devine_bms.api.setup_api import build_setup_router
from devine_bms.api.timezones import iana_zone
from devine_bms.auth.service import AuthService
from devine_bms.runtime import Runtime
from devine_bms.runtime_model import RuntimeConfig, RuntimePoint
from devine_bms.setup.discover import DiscoverManager
from devine_bms.setup.service import SetupService
from devine_bms.values import LiveValue, Status

log = logging.getLogger(__name__)

MAX_RANGE = timedelta(days=31)
PRUNE_EVERY_S = 6 * 3600  # audit log retention check (main spec 5.6: kept for 1 year)
RuntimeFactory = Callable[[], Awaitable[tuple[Runtime, Callable[[], Awaitable[None]]]]]


async def default_runtime() -> tuple[Runtime, Callable[[], Awaitable[None]]]:
    """Full mode: run the site in PostgreSQL, importing the config file only into an empty database."""
    from devine_bms.db.session import make_engine, make_sessionmaker
    from devine_bms.logging_setup import setup_logging
    from devine_bms.settings import get_settings
    from devine_bms.setup.startup import prepare_site
    from devine_bms.storage.postgres import PostgresStorage

    settings = get_settings()
    setup_logging(settings.log_level)
    engine = make_engine(settings.database_url)
    sessions = make_sessionmaker(engine)
    site_name = await prepare_site(sessions, settings)
    runtime = Runtime(PostgresStorage(sessions), site_name)
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
        sessions = getattr(runtime.storage, "sessionmaker", None)
        if sessions is not None:  # setup and logins need the database
            state["setup"] = SetupService(sessions, runtime.site_name, runtime.reload)
            state["discover"] = DiscoverManager(runtime, state["setup"])
            state["auth"] = AuthService(sessions, runtime.site_name)
            state["prune"] = asyncio.create_task(_prune_forever(state["auth"]), name="audit retention")
        try:
            yield
        finally:
            if state.get("prune") is not None:
                state["prune"].cancel()
            if state.get("discover") is not None:
                await state["discover"].stop_all()
            await runtime.stop()
            await cleanup()

    app = FastAPI(title="Devine BMS", version=__version__, lifespan=lifespan)
    install_guard(app, lambda: state.get("auth"))
    app.include_router(build_auth_router(lambda: state.get("auth")))
    app.include_router(build_setup_router(lambda: state.get("setup"), lambda: state.get("runtime"),
                                          lambda: state.get("discover")))

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
            "cov": [
                {"device": c.device.name, "subscribed": sorted(c.state.subscribed),
                 "failed": c.state.failed, "notifications": c.state.notifications}
                for c in rt.cov
            ],
            "connections": [
                {"name": b.name, "where": b.driver.where, "shared": b.driver.shared, "online": b.online,
                 "trunkStats": getattr(b.driver, "stats", None),
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
        with a snapshot of current values, then one message per change. Needs a login, as
        every route does; the session is checked again every minute."""
        auth: AuthService | None = state.get("auth")
        client = client_ip(ws.client.host if ws.client else None, ws.headers.get("x-forwarded-for"))
        token = ws.cookies.get(COOKIE)
        await ws.accept()
        if auth is not None and await auth.actor_for(token, client) is None:
            # Accepted first so the browser sees the reason (closing before accepting is a bare 403).
            await ws.close(code=4401, reason="log in first")
            return
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

        async def still_signed_in() -> None:
            while auth is not None:
                await asyncio.sleep(60)
                if await auth.actor_for(token, client, touch=False) is None:
                    await ws.close(code=4401, reason="session ended")
                    return

        tasks = [asyncio.create_task(receive()), asyncio.create_task(send()),
                 asyncio.create_task(still_signed_in())]
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


async def _prune_forever(auth: AuthService) -> None:
    while True:
        try:
            removed = await auth.prune()
            if removed:
                log.info("audit log: removed %d entries older than a year", removed)
        except Exception as exc:  # keep trying; the log only grows meanwhile
            log.warning("audit log retention failed: %s", exc)
        await asyncio.sleep(PRUNE_EVERY_S)


def _default_web_dist() -> Path | None:
    from devine_bms.settings import get_settings

    path = get_settings().web_dist
    return Path(path) if path else None


app = create_app(web_dist=_default_web_dist())
