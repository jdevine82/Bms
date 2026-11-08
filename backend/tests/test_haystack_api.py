"""The Haystack API through a Haystack client library (phable), against a running backend on a
real port (milestone 12a-Haystack; main spec 5.12 and 16, "Haystack conformance tests").

Needs BMS_TEST_DATABASE_URL."""

from __future__ import annotations

import asyncio
import json
import os
import socket
from datetime import date, timedelta

import httpx
import pytest

if not os.environ.get("BMS_TEST_DATABASE_URL"):
    pytest.skip("BMS_TEST_DATABASE_URL is not set", allow_module_level=True)
pytestmark = pytest.mark.db

phable = pytest.importorskip("phable")

API_USER, API_PASSWORD = "analytics", "api client password 1"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def site_data(sim_port: int) -> dict:
    from helpers import example_data

    data = example_data(sim_port)
    data["spaces"] = [{"name": "plant", "dis": "Plant room", "tags": {"mechanical": True}}]
    data["equips"][0]["space"] = "plant"
    data["equips"].append({"name": "board1", "dis": "Distribution board", "space": "plant",
                           "parent": "meter1", "tags": {"elec": True, "panel": True, "myCustom": True}})
    data["feeds"] = [{"from": "meter1", "to": "board1", "medium": "elec"}]
    for p in data["points"]:
        p["his"] = True
    return data


async def stack(tmp_path):
    import uvicorn

    from devine_bms.api.app import create_app
    from devine_bms.config.sync import sync_config
    from devine_bms.config.validate import parse_config
    from devine_bms.db.session import make_sessionmaker
    from devine_bms.runtime import Runtime
    from devine_bms.settings import Settings
    from devine_bms.storage.postgres import PostgresStorage
    from helpers import TEST_INTERVALS
    from setup_helpers import ADMIN, URL, fresh_database
    from simulators.modbus_meter import MeterSimulator

    sim = MeterSimulator(port=0, update_interval_s=0.05)
    await sim.start()
    engine = await fresh_database()
    sessions = make_sessionmaker(engine)
    config, _ = parse_config(site_data(sim.port))
    async with sessions() as session, session.begin():
        await sync_config(session, config)

    async def factory():
        runtime = Runtime(PostgresStorage(sessions), "dev_site", intervals=TEST_INTERVALS, stale_check_s=0.05,
                          flush_interval_s=0.1)
        runtime.settings = Settings(database_url=URL)

        async def cleanup():
            pass

        return runtime, cleanup

    app = create_app(factory, alarm_tick_s=0.1)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    base = f"http://127.0.0.1:{port}"
    try:
        async with httpx.AsyncClient(base_url=base, timeout=30) as admin:
            assert (await admin.post("/api/auth/first-admin", json=ADMIN)).status_code == 200
            r = await admin.post("/api/users", json={"username": API_USER, "dis": "Analytics platform",
                                                     "apiClient": True, "role": "admin", "platform": True,
                                                     "password": API_PASSWORD})
            assert r.status_code == 200, r.text
            yield {"admin": admin, "base": base, "uri": f"{base}/api/haystack", "app": app, "sim": sim}
    finally:
        server.should_exit = True
        await task
        await sim.stop()
        await engine.dispose()


async def switch(admin, on: bool):
    r = await admin.put("/api/auth/settings", json={"haystackApi": on})
    assert r.status_code == 200, r.text


def site_today() -> date:
    """Today in the example site's time zone: a range of "today" is the site's day."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Australia/Sydney")).date()


def meta_grid(meta: dict):
    """A request with meta and no rows, as phable sends one."""
    from phable import Grid as PGrid
    from phable import GridCol

    return PGrid(meta={"ver": "3.0", **meta}, cols=[GridCol("empty")], rows=[])


def connect(uri, user=API_USER, password=API_PASSWORD, content_type="json"):
    return phable.open_haystack_client(uri, user, password, content_type=content_type)


async def wait_for(check, timeout=10.0):
    end = asyncio.get_running_loop().time() + timeout
    while not await check():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("timed out")
        await asyncio.sleep(0.1)


async def test_off_by_default_and_api_clients_only(tmp_path):
    async for s in stack(tmp_path):
        admin = s["admin"]
        users = {u["username"]: u for u in (await admin.get("/api/users")).json()}
        # An API client is a viewer whatever was asked, and never a platform account.
        assert users[API_USER]["apiClient"] and users[API_USER]["role"] == "viewer" and not users[API_USER]["platform"]
        async with httpx.AsyncClient(base_url=s["base"]) as c:
            assert (await c.get("/api/haystack/about")).status_code == 404  # off
        await switch(admin, True)
        async with httpx.AsyncClient(base_url=s["base"]) as c:
            r = await c.get("/api/haystack/about")
            assert r.status_code == 401 and r.headers["www-authenticate"] == "HELLO"
            # An API client cannot log in to the web app.
            r = await c.post("/api/auth/login", json={"username": API_USER, "password": API_PASSWORD})
            assert r.status_code == 403 and "Haystack API" in r.json()["error"]
        # A person's account cannot use the API (decision 032).
        with pytest.raises(phable.AuthError):
            await asyncio.to_thread(lambda: connect(s["uri"], "admin", "correct horse battery").__enter__())
        with pytest.raises(phable.AuthError):
            await asyncio.to_thread(lambda: connect(s["uri"], API_USER, "wrong password!!").__enter__())

        def about():
            with connect(s["uri"]) as client:
                return client.about(), client._auth_token

        info, token = await asyncio.to_thread(about)
        assert info["productName"] == "Devine BMS" and info["haystackVersion"] == "4.0"
        assert info["whoami"] == API_USER and info["tz"] == "Sydney"
        # The token is not a web session; a web session is not an API token; close logged it out.
        async with httpx.AsyncClient(base_url=s["base"], cookies={"bms_session": token}) as c:
            assert (await c.get("/api/points")).status_code == 401
            assert (await c.get("/api/haystack/read?filter=point",
                                headers={"Authorization": f"BEARER authToken={token}"})).status_code == 401
        assert (await admin.get("/api/haystack/about")).status_code == 401
        audit = (await admin.get("/api/audit?action=api-login")).json()
        assert audit and audit[0]["user"] == API_USER
        failed = (await admin.get("/api/audit?action=login-failed")).json()
        assert any("Haystack API" in e["summary"] for e in failed)
        await switch(admin, False)
        async with httpx.AsyncClient(base_url=s["base"]) as c:
            assert (await c.get("/api/haystack/about")).status_code == 404


@pytest.mark.parametrize("content_type", ["json", "zinc"])
async def test_read_nav_his_and_watches_with_phable(tmp_path, content_type):
    from phable import Grid as PGrid
    from phable import Ref as PRef

    async for s in stack(tmp_path):
        await switch(s["admin"], True)
        app = s["app"]

        async def polled():
            rt = app.state.services["runtime"]
            return all((v := rt.cache.get(p.id)) is not None and v.status.value == "ok" for p in rt.config.points)

        await wait_for(polled)
        await asyncio.sleep(1.0)  # some history

        def work():
            with connect(s["uri"], content_type=content_type) as c:
                out = {}
                out["site"] = c.read("site")
                out["points"] = c.read_all("point")
                out["limited"] = c.read_all("point", limit=2)
                out["equip_pts"] = c.read_all('point and equipRef->dis == "Main power meter" and unit == "kW"')
                power = next(r for r in out["points"].rows if r["devineName"] == "site_meter1_power")
                out["byid"] = c.read_by_id(power["id"])
                today = site_today()  # the site's date (Sydney), not this machine's
                out["his"] = c.his_read_by_id(power["id"], today)
                out["his2"] = c.his_read_by_ids([power["id"], out["byid"]["id"]], today)
                out["nav_root"] = c.call("nav")
                sub = c.call("watchSub", PGrid.to_grid([{"id": power["id"]}], {"watchDis": "test",
                                                                                  "lease": phable.Number(2, "min")}))
                out["sub"] = sub
                wid = sub.meta["watchId"]
                out["poll_all"] = c.call("watchPoll", meta_grid({"watchId": wid, "refresh": phable.Marker()}))
                out["unsub"] = c.call("watchUnsub", meta_grid({"watchId": wid, "close": phable.Marker()}))
                try:
                    c.call("watchPoll", meta_grid({"watchId": wid}))
                    out["poll_closed"] = "no error"
                except phable.CallError as exc:
                    out["poll_closed"] = str(exc.help_msg.meta.get("dis"))
                try:
                    c.call("read", PGrid.to_grid({"filter": "point and"}))
                    out["bad_filter"] = "no error"
                except phable.CallError as exc:
                    out["bad_filter"] = str(exc.help_msg.meta.get("dis"))
                try:  # an unknown id gives an empty row, which a client reports as an unknown record
                    c.read_by_ids([PRef("00000000-0000-0000-0000-000000000000"), power["id"]])
                    out["missing"] = "no error"
                except phable.UnknownRecError:
                    out["missing"] = "unknown"
                return out

        out = await asyncio.to_thread(work)
        site = out["site"]
        assert site["dis"] == "Development site" and site["tz"] == "Sydney" and "geoCoord" in site
        pts = out["points"].rows
        assert len(pts) == 5 and len(out["limited"].rows) == 2
        assert [r["devineName"] for r in out["equip_pts"].rows] == ["site_meter1_power"]
        power = out["byid"]
        assert power["curStatus"] == "ok" and power["curVal"].unit == "kW" and "sensor" in power
        assert power["equipRef"].val and power["spaceRef"].val and power["siteRef"].val
        his = out["his"]
        assert his.rows and all(str(r["ts"].tzinfo) == "Australia/Sydney" for r in his.rows)
        assert his.rows[0]["val"].unit == "kW"
        assert len(out["his2"].cols) == 3
        assert [r["dis"] for r in out["nav_root"].rows] == ["Development site"]
        assert out["sub"].rows[0]["curStatus"] == "ok" and out["poll_all"].rows
        assert "unknown or expired watch" in out["poll_closed"]
        assert "filter" in out["bad_filter"]
        assert out["missing"] == "unknown"


async def test_model_conformance_and_export(tmp_path):
    """Every entity against the Haystack 4.0.0 and house libraries (main spec 16): one entity type,
    one point type, a valid kind, refs that resolve, units on numbers; custom tags are allowed."""
    from devine_bms.haystack.codec import grid_from_hayson
    from devine_bms.haystack.kinds import MARKER, Ref
    from devine_bms.haystack.library import TagSource, load_library

    lib = load_library()
    async for s in stack(tmp_path):
        admin = s["admin"]
        r = await admin.get("/api/haystack-export?format=json")
        assert r.status_code == 200 and 'filename="dev_site-haystack.json"' in r.headers["content-disposition"]
        grid = grid_from_hayson(json.loads(r.text))
        ents = {e["id"].val: e for e in grid.rows}
        assert sorted(sum(1 for e in grid.rows if k in e) for k in ("site", "space", "equip", "point")) == [1, 1, 2, 5]
        custom = set()
        for e in grid.rows:
            assert sum(1 for k in ("site", "space", "equip", "point") if e.get(k) is MARKER) == 1, e
            for k, v in e.items():
                if isinstance(v, Ref) and k != "id":
                    assert v.val in ents, f"{k} of {e['dis']} does not resolve"
                if lib.source_of(k) == TagSource.CUSTOM:
                    custom.add(k)
            assert "curVal" not in e and "curStatus" not in e  # the export has no live values
            if "point" in e:
                assert sum(1 for k in ("sensor", "sp", "cmd") if k in e) == 1
                assert e["kind"] in ("Number", "Bool", "Str")
                assert e["kind"] != "Number" or e.get("unit") or e["devineName"] == "site_meter1_pf"
                assert e["siteRef"] and (e.get("equipRef") or e.get("spaceRef"))
        assert custom == {"myCustom", "mechanical"}, custom  # site custom tags, served as plain tags (5.2)
        board = next(e for e in grid.rows if e.get("dis") == "Distribution board")
        meter = next(e for e in grid.rows if e.get("dis") == "Main power meter")
        assert board["equipRef"].val == meter["id"].val and board["elecRef"].val == meter["id"].val
        z = await admin.get("/api/haystack-export?format=zinc")
        assert z.text.startswith('ver:"3.0"') and z.headers["content-type"].startswith("text/zinc")
        async with httpx.AsyncClient(base_url=s["base"]) as anon:
            assert (await anon.get("/api/haystack-export")).status_code == 401


async def test_his_limits_and_rate_limit(tmp_path):
    from phable import Grid as PGrid

    async for s in stack(tmp_path):
        await switch(s["admin"], True)

        def work():
            c = phable.HaystackClient.open(s["uri"], API_USER, API_PASSWORD)  # no close: it would be rate-limited
            p = c.read("point and unit == \"kW\"")
            errors = []
            for rng in (f"{date.today() - timedelta(days=40)},{date.today()}", "last week"):
                try:
                    c.call("hisRead", PGrid.to_grid({"id": p["id"], "range": rng}))
                except phable.CallError as exc:
                    errors.append(exc.help_msg.meta["dis"])
            codes = []
            import urllib.request
            for _ in range(35):
                req = urllib.request.Request(f"{s['uri']}/ops", headers={
                    "Authorization": f"BEARER authToken={c._auth_token}", "Accept": "application/json"})
                try:
                    urllib.request.urlopen(req).close()
                    codes.append(200)
                except urllib.error.HTTPError as e:
                    codes.append(e.code)
            return errors, codes

        import urllib.error  # noqa: F401 -- used in the thread

        errors, codes = await asyncio.to_thread(work)
        assert "at most 31 days" in errors[0] and "range" in errors[1]
        assert 429 in codes and codes.index(429) >= 25
