"""HMI standby takeover and failback (milestone 11, decision 029).

Listening to the serial buses (MS/TP frame headers, quiet time), the Pi's copy of the setup, and
the whole round with a real server and a real Pi backend on two ports and two databases: the
Pi in standby (held, commands refused), the server stops, the Pi takes over (direct mode: it
polls and commands; Critical alarm), the server comes back held by the HMI, the operator hands
control back with the PIN (hand modes released, the audit log sent), and the server resumes.

The round needs BMS_TEST_DATABASE_URL and BMS_TEST_PI_DATABASE_URL (a second, empty database:
createdb -O bms bms_test_pi).
"""

from __future__ import annotations

import asyncio
import os
import socket
import time

import httpx
import pytest

from devine_bms.standby.hmi import localise, parse_ports
from devine_bms.standby.listen import (
    SerialListener,
    _header_crc_ok,
    mstp_frame,
    mstp_sources,
)

TOKEN = "test standby token 0123456789"


# ---------- listening ----------

def test_the_mstp_header_crc_matches_bacnet_stack():
    # bacnet-stack's own test vector: header 00 10 05 00 00 has CRC 0x8C.
    assert mstp_frame(0, 0x10, 0x05).hex() == "55ff00100500008c"
    assert _header_crc_ok(bytes.fromhex("00100500008c"))
    assert not _header_crc_ok(bytes.fromhex("00100500008d"))


def test_mstp_sources_finds_frames_split_and_with_noise():
    token = mstp_frame(0, 1, 0)  # a token from MAC 0 (the server) to MAC 1
    data = mstp_frame(5, 0, 7, b"\x01\x02\x03")  # data from MAC 7, with its data and CRC
    stream = b"\x00\x55" + token + b"\xff\x55\xff\x99" + data + token
    sources, keep = mstp_sources(bytearray(stream[:13]))
    assert sources == [0]
    more, keep = mstp_sources(keep + bytearray(stream[13:]))
    assert more == [7, 0] and keep == bytearray()
    # A broken header is skipped, not counted.
    bad = bytearray(token)
    bad[-1] ^= 0xFF
    assert mstp_sources(bad)[0] == []


def _pty() -> tuple[int, str]:
    master, slave = os.openpty()
    import tty

    tty.setraw(slave)
    return master, os.ttyname(slave)


def test_a_modbus_line_is_clear_after_the_quiet_time():
    master, port = _pty()
    lst = SerialListener("rs485_1", "modbus_rtu", port, 9600)
    lst.start()
    try:
        time.sleep(0.3)
        os.write(master, b"\x01\x03\x00\x00\x00\x02\xc4\x0b")  # the server polling
        time.sleep(0.2)
        assert lst.last_heard is not None and not lst.clear(0.5)
        time.sleep(0.6)
        assert lst.clear(0.5), lst.state(0.5)
        assert lst.state(0.5)["clear"] and lst.error is None
    finally:
        lst.stop()
        os.close(master)


def test_an_mstp_trunk_counts_only_frames_from_mac_0():
    master, port = _pty()
    lst = SerialListener("trunk1", "bacnet_mstp", port, 38400)
    lst.start()
    try:
        time.sleep(0.3)
        os.write(master, mstp_frame(0, 2, 5) + mstp_frame(1, 0xFF, 5))  # other nodes talking
        time.sleep(0.5)
        assert lst.frames == 2 and lst.last_heard is None
        os.write(master, mstp_frame(0, 1, 0))  # the server's node passing the token
        time.sleep(0.5)
        assert lst.frames == 3 and lst.last_heard is not None and not lst.clear(2.0)
    finally:
        lst.stop()
        os.close(master)


def test_a_port_that_cannot_open_is_never_clear():
    lst = SerialListener("rs485_1", "modbus_rtu", "/dev/does-not-exist", 9600)
    lst.start()
    time.sleep(0.3)
    lst.stop()
    assert lst.error and not lst.clear(0)


def test_the_pi_uses_its_own_ports_and_mac_1():
    config = {"buses": [
        {"name": "rs485_1", "type": "modbus_rtu", "port": "/dev/ttyServer1"},
        {"name": "trunk1", "type": "bacnet_mstp", "port": "/dev/ttyServer2", "service": "172.17.0.1:47901",
         "mac": 0, "router": {"interface": "eth1:m1", "address": "192.168.20.5"}},
        {"name": "gw", "type": "modbus_tcp_gateway", "host": "10.0.0.5", "port": 502}]}
    ports = parse_ports("rs485_1=/dev/ttyBMS-rs485-1, trunk1=/dev/ttyBMS-trunk1")
    out = localise(config, ports)
    assert out["buses"][0]["port"] == "/dev/ttyBMS-rs485-1"
    assert out["buses"][1] == {"name": "trunk1", "type": "bacnet_mstp", "port": "/dev/ttyBMS-trunk1",
                               "service": "127.0.0.1:47901", "mac": 1}
    assert out["buses"][2]["port"] == 502  # not a serial bus
    assert config["buses"][1]["mac"] == 0  # the server's copy is untouched


# ---------- the whole round ----------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Served:
    """A backend on a real port (the HMI and the server talk over HTTP)."""

    def __init__(self, app, port: int) -> None:
        import uvicorn

        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                                    lifespan="on"))
        self.task: asyncio.Task | None = None

    async def __aenter__(self):
        self.task = asyncio.create_task(self.server.serve())
        while not self.server.started:
            if self.task.done():
                await self.task
            await asyncio.sleep(0.05)
        return self

    async def __aexit__(self, *exc):
        self.server.should_exit = True
        await self.task


async def wait_for(check, timeout=10.0, what="the condition"):
    end = asyncio.get_running_loop().time() + timeout
    while not await check():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.1)


@pytest.mark.db
async def test_takeover_and_failback(tmp_path):
    if not (os.environ.get("BMS_TEST_DATABASE_URL") and os.environ.get("BMS_TEST_PI_DATABASE_URL")):
        pytest.skip("BMS_TEST_DATABASE_URL and BMS_TEST_PI_DATABASE_URL are needed")
    from helpers import TEST_INTERVALS
    from setup_helpers import ADMIN, URL, fresh_database
    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import create_async_engine
    from test_commands import bus_stack, limit_value, site

    from devine_bms.api.app import create_app
    from devine_bms.config.sync import sync_config
    from devine_bms.config.validate import parse_config
    from devine_bms.db import models as db
    from devine_bms.db.session import make_sessionmaker
    from devine_bms.runtime import Runtime
    from devine_bms.settings import Settings
    from devine_bms.standby.boot import HELD_WHY, prepare_standby
    from devine_bms.storage.postgres import PostgresStorage

    pi_url_db = os.environ["BMS_TEST_PI_DATABASE_URL"]
    server_port, pi_port = _free_port(), _free_port()
    server_url, pi_url = f"http://127.0.0.1:{server_port}", f"http://127.0.0.1:{pi_port}"
    sim = await bus_stack()

    # The server: the site in its database, and the HMI it knows.
    engine = await fresh_database()
    sessions = make_sessionmaker(engine)
    config, _ = parse_config(site(sim.port, 1))
    async with sessions() as session, session.begin():
        await sync_config(session, config)
    server_settings = Settings(database_url=URL, standby_token=TOKEN, hmi_urls=pi_url, heartbeat_s=0.3)

    def server_app():
        async def factory():
            runtime = Runtime(PostgresStorage(sessions), "dev_site", intervals=TEST_INTERVALS, stale_check_s=0.05,
                              flush_interval_s=0.1)
            runtime.settings = server_settings

            async def cleanup():
                pass

            return runtime, cleanup

        return create_app(factory, alarm_tick_s=0.1, override_check_s=0.2, schedule_tick_s=0.2, logic_tick_s=0.2)

    # The Pi: an empty database (PostgreSQL in RAM), the same schema.
    pi_engine = create_async_engine(pi_url_db)
    async with pi_engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    from alembic.config import Config
    from setup_helpers import BACKEND

    from alembic import command

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", pi_url_db)
    cfg.attributes["configure_logger"] = False
    await asyncio.to_thread(command.upgrade, cfg, "head")
    pi_sessions = make_sessionmaker(pi_engine)
    pi_settings = Settings(database_url=pi_url_db, mode="standby", server_url=server_url, standby_token=TOKEN,
                           hmi_name="hmi-plant", standby_cache=str(tmp_path / "standby-cache.json"),
                           heartbeat_s=0.2, takeover_after_s=1.0, bus_quiet_s=0.5, failback_after_s=1.0)

    async def pi_factory():
        name = await prepare_standby(pi_sessions, pi_settings, retry_s=0.2)
        runtime = Runtime(PostgresStorage(pi_sessions), name, intervals=TEST_INTERVALS, stale_check_s=0.05,
                          flush_interval_s=0.1)
        runtime.settings, runtime.standby = pi_settings, True

        async def cleanup():
            pass

        return runtime, cleanup

    pi_app = create_app(pi_factory, alarm_tick_s=0.1, override_check_s=0.2, schedule_tick_s=0.2, logic_tick_s=0.2)
    srv = await Served(server_app(), server_port).__aenter__()
    try:
        async with httpx.AsyncClient(base_url=server_url) as s:
            assert (await s.post("/api/auth/first-admin", json=ADMIN)).status_code == 200
            assert (await s.post("/api/auth/me/pin", json={"pin": "4826", "password": ADMIN["password"]})).status_code == 200
            # Only with the token.
            assert (await s.get("/api/standby/cache")).status_code == 403
            assert (await s.get("/api/standby/cache", headers={"X-BMS-Standby-Token": "wrong"})).status_code == 403
            cache = (await s.get("/api/standby/cache", headers={"X-BMS-Standby-Token": TOKEN})).json()
            assert [p["username"] for p in cache["people"]] == ["admin"] and "password" not in str(cache["people"])

        async with Served(pi_app, pi_port), httpx.AsyncClient(base_url=pi_url) as pi:
            # ---------- standby ----------
            assert (await pi.get("/api/mode")).json() == {"mode": "standby"}
            st = (await pi.get("/api/standby/state")).json()
            assert st["mode"] == "standby" and st["name"] == "hmi-plant" and st["cacheRevision"] == cache["revision"]
            assert (tmp_path / "standby-cache.json").is_file()
            await wait_for(lambda: _true(pi_app.state.services["standby"].server_ok), what="the heartbeat")
            pi_runtime = pi_app.state.services["runtime"]
            assert pi_runtime.held and pi_runtime.held_why == HELD_WHY and pi_runtime.buses == []
            # The PIN works on the Pi's own screen; a command is refused while in standby.
            r = await pi.post("/api/auth/pin", json={"username": "admin", "pin": "4826"})
            assert r.status_code == 200, r.text
            pi.cookies = r.cookies
            limit = next(p["id"] for p in (await pi.get("/api/points")).json() if p["name"] == "site_meter2_limit")
            r = await pi.post(f"/api/points/{limit}/command", json={"value": 120, "mode": "hand", "hours": 1,
                                                                   "confirm": True})
            assert r.status_code >= 400 and "standby" in r.text, r.text
            async with httpx.AsyncClient(base_url=server_url) as s:
                await s.post("/api/auth/login", json={"username": "admin", "password": ADMIN["password"]})
                gate = (await s.get("/api/standby/gate")).json()
                assert gate["hmis"][0]["state"] == "standby" and gate["heldBy"] == []

            # ---------- the server stops: takeover ----------
            await srv.__aexit__(None, None, None)
            srv = None

            async def direct():
                return (await pi.get("/api/standby/state")).json()["mode"] == "direct"

            await wait_for(direct, timeout=8, what="the takeover")
            assert not pi_runtime.held and pi_runtime.buses

            async def critical():
                alarms = (await pi.get("/api/alarms")).json()
                alarm_list = alarms if isinstance(alarms, list) else alarms.get("alarms", [])
                return any("Direct mode" in a["message"] and a["level"] == "critical" for a in alarm_list)

            await wait_for(critical, what="the Critical direct-mode alarm")

            async def polled():
                live = pi_runtime.cache.get(_uuid(limit))
                return live is not None and live.status.value == "ok"

            await wait_for(polled, what="the Pi polling")
            r = await pi.post(f"/api/points/{limit}/command", json={"value": 120, "mode": "hand", "hours": 1,
                                                                   "confirm": True})
            assert r.status_code == 200, r.text
            await wait_for(lambda: _true(abs(limit_value(sim) - 120) < 0.01), what="the Pi's write")
            # Not before the server is back.
            r = await pi.post("/api/standby/failback")
            assert r.status_code == 409 and "not back" in r.json()["error"]

            # ---------- the server comes back: held by the HMI ----------
            srv = await Served(server_app(), server_port).__aenter__()
            async with httpx.AsyncClient(base_url=server_url) as s:
                assert "hmi-plant" in ((await s.get("/api/health")).json()["held"] or "")
                await s.post("/api/auth/login", json={"username": "admin", "password": ADMIN["password"]})
                # Point IDs are the server's own: the Pi's database made its own.
                s_limit = next(p["id"] for p in (await s.get("/api/points")).json() if p["name"] == "site_meter2_limit")
                r = await s.post(f"/api/points/{s_limit}/command", json={"value": 50, "confirm": True})
                assert r.status_code >= 400 and "direct mode" in r.text, r.text
                assert abs(limit_value(sim) - 120) < 0.01

                async def ready():
                    return (await pi.get("/api/standby/state")).json()["canFailback"]

                await wait_for(ready, what="the server healthy long enough")
                r = await pi.post("/api/standby/failback")
                assert r.status_code == 200, r.text
                body = r.json()
                assert body["mode"] == "standby" and body["released"] == 1 and body["auditSent"] >= 3
                assert pi_runtime.held and (await pi.get("/api/standby/state")).json()["mode"] == "standby"

                # The server resumes on its next look at the HMI, and has the HMI's audit log.
                async def resumed():
                    return (await s.get("/api/health")).json()["held"] is None

                await wait_for(resumed, what="the server resuming")
                async with sessions() as session:
                    rows = (await session.scalars(select(db.AuditEntry).where(db.AuditEntry.client == "hmi:hmi-plant")
                                                  .order_by(db.AuditEntry.ts))).all()
                summaries = [r.summary for r in rows]
                assert all(x.startswith("[HMI hmi-plant, direct mode]") for x in summaries)
                assert any("took over" in x for x in summaries) and any("handed back" in x for x in summaries)
                assert any(r.username == "admin" and r.action == "command" for r in rows), summaries
                r = await s.post(f"/api/points/{s_limit}/command", json={"value": 50, "confirm": True})
                assert r.status_code == 200, r.text
                await wait_for(lambda: _true(abs(limit_value(sim) - 50) < 0.01), what="the server's write")
    finally:
        if srv is not None:
            await srv.__aexit__(None, None, None)
        await sim.stop()
        await pi_engine.dispose()
        await engine.dispose()


async def _true(value) -> bool:
    return bool(value)


def _uuid(text_id):
    import uuid

    return uuid.UUID(text_id)
