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
from devine_bms.alarms.delivery import Delivery
from devine_bms.alarms.engine import AlarmEngine
from devine_bms.api.alarms_api import build_alarms_router
from devine_bms.api.auth_api import COOKIE, build_auth_router, client_ip, install_guard, set_trusted_proxies
from devine_bms.api.commands_api import build_commands_router
from devine_bms.api.graphics_api import build_graphics_router
from devine_bms.api.history import history_csv, row_json, thin
from devine_bms.api.logic_api import build_logic_router
from devine_bms.api.schedules_api import build_schedules_router
from devine_bms.api.setup_api import build_setup_router
from devine_bms.api.standalone_api import build_standalone_router
from devine_bms.api.standby_api import build_standby_router
from devine_bms.api.counters_api import build_counters_router
from devine_bms.api.haystack_api import build_haystack_router
from devine_bms.api.mqtt_logins_api import build_mqtt_logins_router
from devine_bms.api.updates_api import build_updates_router
from devine_bms.api.timezones import iana_zone
from devine_bms.auth.service import AuthService
from devine_bms.commands.service import CommandService
from devine_bms.logic.engine import LogicEngine
from devine_bms.logic.service import LogicService
from devine_bms.runtime import Runtime
from devine_bms.runtime_model import RuntimeConfig, RuntimePoint
from devine_bms.schedules.service import ScheduleService
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
    if settings.mode == "standalone":  # milestone 11a: PostgreSQL in RAM, the state file on the card
        from devine_bms.standalone.boot import (
            LOG_EVERY_S,
            STANDALONE_INTERVALS,
            make_keeper,
            prepare_standalone,
        )

        site_name, standalone = await prepare_standalone(sessions, settings)
        runtime = Runtime(PostgresStorage(sessions), site_name, intervals=STANDALONE_INTERVALS,
                          history_min_interval_s=LOG_EVERY_S)
        runtime.keeper = make_keeper(sessions, site_name, settings, standalone)
        runtime.settings = settings
        return runtime, engine.dispose
    if settings.mode == "standby":  # milestone 11: an HMI that can take over; the site from its cache
        from devine_bms.standalone.boot import LOG_EVERY_S, STANDALONE_INTERVALS
        from devine_bms.standby.boot import prepare_standby

        site_name = await prepare_standby(sessions, settings)
        runtime = Runtime(PostgresStorage(sessions), site_name, intervals=STANDALONE_INTERVALS,
                          history_min_interval_s=LOG_EVERY_S)
        runtime.settings, runtime.standby = settings, True
        return runtime, engine.dispose
    site_name = await prepare_site(sessions, settings)
    runtime = Runtime(PostgresStorage(sessions), site_name)
    runtime.settings = settings
    return runtime, engine.dispose


async def _release_operator_priority(runtime) -> None:
    """Standalone mode after a power loss (main spec 3.1, decided): hand modes return to Auto.
    Nothing remembers them, so release the operator priority on every writable BACnet point."""
    from devine_bms.commands.writer import MANUAL_PRIORITY, WriteError, write_point
    from devine_bms.config.model import BacnetAddress

    await asyncio.sleep(5)  # let the links come up
    for point in [p for p in runtime.config.points if p.writable and isinstance(p.address, BacnetAddress)]:
        try:
            await write_point(runtime, point, None, MANUAL_PRIORITY)
        except WriteError as exc:
            log.info("return to auto: %s: %s", point.name, exc)


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


def create_app(runtime_factory: RuntimeFactory = default_runtime, web_dist: Path | None = None,
               alarm_tick_s: float = 1.0, override_check_s: float = 5.0, schedule_tick_s: float = 15.0,
               logic_tick_s: float = 1.0) -> FastAPI:
    state: dict = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        runtime, cleanup = await runtime_factory()
        settings = getattr(runtime, "settings", None)
        if settings is not None:
            set_trusted_proxies(getattr(settings, "trusted_proxies", ""))
        standby = bool(getattr(runtime, "standby", False))
        if standby:  # milestone 11: the HMI in standby polls nothing and sends nothing
            from devine_bms.standby.boot import HELD_WHY

            await runtime.start(held=True, why=HELD_WHY)
        else:
            if settings is not None and getattr(settings, "hmi_urls", ""):
                from devine_bms.standby.server import HmiGate

                # Before polling starts: an HMI in direct mode still drives the buses (milestone 11).
                state["gate"] = HmiGate(runtime, settings.hmi_urls.split(","), settings.standby_token,
                                        every_s=settings.heartbeat_s)
                await state["gate"].check()
            await runtime.start(held=runtime.held, why=runtime.held_why)
        state["runtime"] = runtime
        sessions = getattr(runtime.storage, "sessionmaker", None)
        state["sessions"] = sessions
        if sessions is not None:  # setup and logins need the database
            async def reload_all() -> None:  # a setup change: the runtime, then logic's view of it
                await runtime.reload()
                if state.get("logic") is not None:
                    await state["logic"].reload()
                if state.get("counters") is not None:
                    await state["counters"].reload()

            state["setup"] = SetupService(sessions, runtime.site_name, reload_all)
            state["discover"] = DiscoverManager(runtime, state["setup"])
            state["auth"] = AuthService(sessions, runtime.site_name)
            state["delivery"] = Delivery(sessions, runtime.site_name, iana_zone(runtime.config.site_tz).key)
            state["alarms"] = AlarmEngine(runtime, sessions, runtime.site_name, state["delivery"].queue_for,
                                          tick_s=alarm_tick_s)
            await state["alarms"].start()
            await state["delivery"].start()
            state["commands"] = CommandService(runtime, sessions, runtime.site_name, state["auth"].record,
                                               check_s=override_check_s)
            await state["commands"].start()
            state["schedules"] = ScheduleService(runtime, sessions, runtime.site_name,
                                                 iana_zone(runtime.config.site_tz).key, state["auth"].record,
                                                 lambda: state["commands"].overrides, tick_s=schedule_tick_s)
            if standby:  # no schedules or logic on an HMI, in standby or in direct mode (main spec 9)
                await state["schedules"].load()
            else:
                await state["schedules"].start()
            state["logic"] = LogicEngine(runtime, sessions, runtime.site_name, lambda: state["commands"].overrides,
                                         state["schedules"], state["auth"].record, tick_s=logic_tick_s)
            # Hand mode ends at logic's value, else the schedule's, else the value before (decision 027).
            state["commands"].schedule_value = lambda p: (
                state["logic"].value_for(p) if state["logic"].owns(p) else state["schedules"].value_for(p))
            runtime.logic_write = state["logic"].write_variable_point  # variables published as points
            if standby:
                await state["logic"].reload()
            else:
                await state["logic"].start()
            if standby:
                from devine_bms.standby.hmi import StandbyService

                state["standby"] = StandbyService(runtime, sessions, runtime.site_name, settings, state["auth"],
                                                  state["commands"], state["alarms"], state["auth"].record,
                                                  heartbeat_s=settings.heartbeat_s)
                state["auth"].extra_kiosks = {"127.0.0.1", "::1"}  # the Pi's own touch screen
                state["delivery"].sms_allowed = False  # the Pi has no modem
                await state["standby"].start()
            if state.get("gate") is not None:
                state["gate"].record = state["auth"].record
                await state["gate"].start()
            keeper = getattr(runtime, "keeper", None)
            if keeper is not None:  # standalone mode (milestone 11a)
                from devine_bms.standalone.service import StandaloneService

                state["standalone"] = StandaloneService(runtime, sessions, runtime.site_name, runtime.settings, keeper,
                                                        iana_zone(runtime.config.site_tz).key, state["auth"].record)
                state["auth"].extra_kiosks = {"127.0.0.1", "::1"}  # the Pi's own touch screen
                state["delivery"].sms_allowed = False  # standalone: on-screen, ntfy and email (main spec 3.1)
                keeper.start()
                await state["standalone"].start()
                await state["standalone"].prepare_setup_code(state["auth"])
                state["release"] = asyncio.create_task(_release_operator_priority(runtime), name="return to auto")
            if settings is not None:  # milestone 12b
                from devine_bms.updates.service import UpdateService

                mode = "standby" if standby else ("standalone" if keeper is not None else "full")
                state["updates"] = UpdateService(settings, state["auth"].record, mode, lambda: state.get("standby"),
                                                 lambda: state.get("gate"))
            from devine_bms.haystack.server import HaystackService  # milestone 12a-Haystack

            state["haystack"] = HaystackService(sessions, runtime.site_name, state["auth"], runtime,
                                                "standby" if standby else "full")
            if not standby:  # maintenance counters (5.10): the server counts; an HMI does not
                from devine_bms.counters.engine import CounterEngine

                try:
                    await state["setup"].ensure_counters()  # counter points for sites set up before counters
                except Exception:  # noqa: BLE001 -- counting is never a reason not to start
                    log.exception("making the counter points failed")
                state["counters"] = CounterEngine(runtime, sessions, runtime.site_name, state["alarms"],
                                                  state["auth"].record)
                await state["counters"].start()
            state["logic_service"] = LogicService(state["logic"], sessions, state["auth"].record, state["setup"])
            state["prune"] = asyncio.create_task(_prune_forever(state["auth"], state["alarms"]), name="retention")
        try:
            yield
        finally:
            if state.get("prune") is not None:
                state["prune"].cancel()
            if state.get("release") is not None:
                state["release"].cancel()
            for name in ("standby", "gate"):
                if state.get(name) is not None:
                    await state[name].stop()
            if state.get("standalone") is not None:
                await state["standalone"].stop()
                await state["standalone"].keeper.stop()  # the last save
            for name in ("counters", "logic", "schedules", "commands", "alarms", "delivery"):
                if name == "logic" and state.get("standby") is not None:
                    continue  # an HMI runs no logic, so it has no outputs to put to their fail-safe
                if state.get(name) is not None:
                    await state[name].stop()
            if state.get("discover") is not None:
                await state["discover"].stop_all()
            await runtime.stop()
            await cleanup()

    app = FastAPI(title="Devine BMS", version=__version__, lifespan=lifespan)
    app.state.services = state  # the running services, for tools and tests
    install_guard(app, lambda: state.get("auth"))
    app.include_router(build_auth_router(lambda: state.get("auth")))
    app.include_router(build_commands_router(lambda: state.get("commands")))
    app.include_router(build_schedules_router(lambda: state.get("schedules")))
    app.include_router(build_alarms_router(lambda: state.get("alarms"), lambda: state.get("delivery"),
                                           lambda: state.get("auth")))
    app.include_router(build_graphics_router(lambda: state.get("setup")))
    app.include_router(build_logic_router(lambda: state.get("logic_service")))
    app.include_router(build_standalone_router(lambda: state.get("standalone"), lambda: state.get("auth"),
                                               lambda: "standby" if state.get("standby") is not None else ""))
    app.include_router(build_standby_router(
        lambda: state.get("sessions"), lambda: state["runtime"].site_name,
        lambda: getattr(getattr(state.get("runtime"), "settings", None), "standby_token", ""),
        lambda: state.get("gate"), lambda: state.get("standby")))
    app.include_router(build_updates_router(lambda: state.get("updates")))
    app.include_router(build_haystack_router(lambda: state.get("haystack")))
    app.include_router(build_counters_router(lambda: state.get("counters")))
    app.include_router(build_mqtt_logins_router(
        lambda: getattr(getattr(state.get("runtime"), "settings", None), "mqtt_users_cmd", ""),
        lambda: state["auth"].record if state.get("auth") else None))
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
            # Commands (milestone 7A): whether it can be commanded, its limits and any override.
            "writable": point.writable,
            "writeLimits": point.write_limits.model_dump() if point.write_limits else None,
            "override": state["commands"].overrides.get(point.id) if state.get("commands") else None,
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
            "held": rt.held_why,  # milestone 11: why nothing is polled (an HMI in direct mode, or standby)
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

        alarm_queue = state["alarms"].subscribe() if state.get("alarms") else None
        override_queue = state["commands"].subscribe() if state.get("commands") else None

        async def send_overrides() -> None:
            """Every connection also gets hand-mode changes (set, released, timed out)."""
            while override_queue is not None:
                await ws.send_json({"type": "override", **(await override_queue.get())})

        async def send_alarms() -> None:
            """Every connection also gets alarm changes (raised, returned, acknowledged)."""
            while alarm_queue is not None:
                await ws.send_json({"type": "alarm", "alarm": await alarm_queue.get()})

        async def still_signed_in() -> None:
            while auth is not None:
                await asyncio.sleep(60)
                if await auth.actor_for(token, client, touch=False) is None:
                    await ws.close(code=4401, reason="session ended")
                    return

        tasks = [asyncio.create_task(receive()), asyncio.create_task(send()),
                 asyncio.create_task(still_signed_in()), asyncio.create_task(send_alarms()),
                 asyncio.create_task(send_overrides())]
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
            if alarm_queue is not None:
                state["alarms"].unsubscribe(alarm_queue)
            if override_queue is not None:
                state["commands"].unsubscribe(override_queue)

    if web_dist and web_dist.is_dir():
        index = web_dist / "index.html"

        # The Pi kiosk opens /hmi; the same app picks its HMI layout from the path (milestone 3 spec 4).
        @app.get("/hmi", include_in_schema=False)
        @app.get("/hmi/{rest:path}", include_in_schema=False)
        async def hmi(rest: str = "") -> FileResponse:
            return FileResponse(index, headers={"Cache-Control": "no-cache"})

        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
    return app


async def _prune_forever(auth: AuthService, alarms: AlarmEngine) -> None:
    """Retention (main spec 5.6): audit entries, closed alarms and deliveries older than a year."""
    while True:
        try:
            removed = await auth.prune()
            if removed:
                log.info("audit log: removed %d entries older than a year", removed)
            removed = await alarms.prune()
            if removed:
                log.info("alarms: removed %d closed alarms older than a year", removed)
        except Exception as exc:  # keep trying; the log only grows meanwhile
            log.warning("audit log retention failed: %s", exc)
        await asyncio.sleep(PRUNE_EVERY_S)


def _default_web_dist() -> Path | None:
    from devine_bms.settings import get_settings

    path = get_settings().web_dist
    return Path(path) if path else None


app = create_app(web_dist=_default_web_dist())
