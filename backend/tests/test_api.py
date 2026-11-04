"""The REST and WebSocket API against the simulated meter (milestone 2 spec 7).

A real uvicorn server runs in the test's event loop, so the simulator, the
runtime and the API all share one loop.
"""

import asyncio
import csv
import io
import json
import socket
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import uvicorn
import websockets

from devine_bms.api.app import create_app
from devine_bms.api.history import MAX_ROWS, thin
from devine_bms.api.timezones import iana_zone
from devine_bms.runtime import Runtime
from devine_bms.storage.base import MemoryStorage
from devine_bms.values import HisRow, Status
from helpers import TEST_INTERVALS, example_data, runtime_config_from
from simulators.modbus_meter import MeterSimulator


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def server():
    sim = MeterSimulator(port=0, update_interval_s=0.05, seed=5)
    await sim.start()
    storage = MemoryStorage(runtime_config_from(example_data(sim.port)))

    async def factory():
        runtime = Runtime(storage, "dev_site", intervals=TEST_INTERVALS, stale_check_s=0.05, flush_interval_s=0.1)

        async def cleanup():
            pass

        return runtime, cleanup

    port = free_port()
    config = uvicorn.Config(create_app(factory), host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    srv = uvicorn.Server(config)
    task = asyncio.create_task(srv.serve())
    while not srv.started:
        await asyncio.sleep(0.02)
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
        yield {"client": client, "port": port, "sim": sim}
    srv.should_exit = True
    await task
    await sim.stop()


async def wait_all_good(client, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        points = (await client.get("/api/points")).json()
        if all(p["cur"]["status"] == "ok" for p in points):
            return points
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"not all Good: {[p['cur']['status'] for p in points]}")
        await asyncio.sleep(0.05)


async def test_health_and_site(server):
    client = server["client"]
    health = (await client.get("/api/health")).json()
    assert health["status"] == "ok" and health["devices"][0]["name"] == "sim_meter"
    assert health["connections"][0]["devices"] == ["sim_meter"] and health["connections"][0]["shared"] is False
    site = (await client.get("/api/site")).json()
    assert site["name"] == "dev_site" and site["tz"] == "Sydney" and site["ianaTz"] == "Australia/Sydney"
    assert [e["name"] for e in site["equips"]] == ["meter1"]


async def test_points_with_live_values(server):
    points = await wait_all_good(server["client"])
    by_name = {p["name"]: p for p in points}
    power = by_name["site_meter1_power"]
    assert power["unit"] == "kW" and power["kind"] == "Number" and power["type"] == "sensor"
    assert power["equip"]["name"] == "meter1"
    assert power["tags"] == {"elec": True, "power": True}
    assert 5.0 < power["cur"]["value"] < 19.0


async def test_point_filters(server):
    client = server["client"]
    assert len((await client.get("/api/points", params={"equip": "meter1"})).json()) == 5
    assert len((await client.get("/api/points", params={"equip": "nope"})).json()) == 0
    energy = (await client.get("/api/points", params={"tag": ["energy", "import"]})).json()
    assert [p["name"] for p in energy] == ["site_meter1_import_energy"]


async def test_one_point_and_404(server):
    client = server["client"]
    point = (await client.get("/api/points")).json()[0]
    assert (await client.get(f"/api/points/{point['id']}")).json()["name"] == point["name"]
    assert (await client.get(f"/api/points/{uuid.uuid4()}")).status_code == 404


async def test_bad_value_is_not_given_as_live(server):
    client, sim = server["client"], server["sim"]
    await wait_all_good(client)
    sim.mode = "exception"
    deadline = asyncio.get_running_loop().time() + 5
    while True:
        power = next(p for p in (await client.get("/api/points")).json() if p["name"] == "site_meter1_power")
        if power["cur"]["status"] == "fault":
            break
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(0.05)
    assert power["cur"]["value"] is None
    assert power["cur"]["lastValue"] is not None
    assert "exception 4" in power["cur"]["reason"]


async def test_history_and_csv(server):
    client = server["client"]
    points = await wait_all_good(client)
    power = next(p for p in points if p["name"] == "site_meter1_power")
    await asyncio.sleep(0.6)
    his = (await client.get(f"/api/points/{power['id']}/history")).json()
    assert his["unit"] == "kW" and not his["thinned"]
    assert len(his["rows"]) >= 3 and all(r["status"] == "ok" for r in his["rows"])

    response = await client.get(f"/api/points/{power['id']}/history.csv")
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0] == ["time (Australia/Sydney)", "site_meter1_power (kW)", "status", "reason"]
    assert rows[1][0].endswith(("+11:00", "+10:00"))  # local time with its offset
    assert len(rows) == len(his["rows"]) + 1


async def test_history_range_checks(server):
    client = server["client"]
    point = (await client.get("/api/points")).json()[0]
    url = f"/api/points/{point['id']}/history"
    assert (await client.get(url, params={"start": "2026-11-02T00:00:00Z", "end": "2026-11-01T00:00:00Z"})).status_code == 422
    assert (await client.get(url, params={"start": "2026-01-01T00:00:00Z", "end": "2026-03-01T00:00:00Z"})).status_code == 422
    assert (await client.get(url, params={"start": "2026-11-01T00:00:00", "end": "2026-11-01T01:00:00"})).status_code == 422


async def test_websocket_snapshot_then_changes(server):
    points = await wait_all_good(server["client"])
    power = next(p for p in points if p["name"] == "site_meter1_power")
    async with websockets.connect(f"ws://127.0.0.1:{server['port']}/api/live") as ws:
        await ws.send(json.dumps({"subscribe": [power["id"]]}))
        snapshot = json.loads(await asyncio.wait_for(ws.recv(), 2))
        assert snapshot["type"] == "snapshot" and snapshot["values"][0]["id"] == power["id"]
        change = json.loads(await asyncio.wait_for(ws.recv(), 2))
        assert change["type"] == "value" and change["id"] == power["id"] and change["status"] == "ok"


async def test_websocket_all_and_bad_message(server):
    await wait_all_good(server["client"])
    async with websockets.connect(f"ws://127.0.0.1:{server['port']}/api/live") as ws:
        await ws.send(json.dumps({"hello": 1}))
        assert json.loads(await asyncio.wait_for(ws.recv(), 2))["type"] == "error"
        await ws.send(json.dumps({"subscribe": "all"}))
        snapshot = json.loads(await asyncio.wait_for(ws.recv(), 2))
        assert len(snapshot["values"]) == 5


def test_thinning_keeps_min_max_and_every_gap():
    pid = uuid.uuid4()
    start = datetime(2026, 11, 1, tzinfo=UTC)
    rows = [HisRow(pid, start + timedelta(seconds=i), float(i % 100), None, Status.OK, None) for i in range(30_000)]
    rows.insert(15_000, HisRow(pid, start + timedelta(seconds=14_999.5), None, None, Status.DOWN, "no answer"))
    kept, thinned = thin(rows, start, start + timedelta(seconds=30_000))
    assert thinned and len(kept) <= MAX_ROWS + 10
    assert any(r.status == Status.DOWN for r in kept)
    assert max(r.num for r in kept if r.num is not None) == 99.0
    assert kept == sorted(kept, key=lambda r: r.ts)


def test_time_zones():
    assert iana_zone("Sydney").key == "Australia/Sydney"
    assert iana_zone("UTC").key == "UTC"
    with pytest.raises(LookupError):
        iana_zone("Atlantis")


async def test_hmi_path_serves_the_web_app(tmp_path):
    """The Pi kiosk opens /hmi; it must get the same index.html as / (milestone 3 spec 4)."""
    (tmp_path / "index.html").write_text("<!doctype html><title>app</title>")

    async def never_started():
        raise AssertionError("the runtime is not needed for static files")

    app = create_app(never_started, web_dist=tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for path in ("/", "/hmi", "/hmi/anything"):
            response = await client.get(path)
            assert response.status_code == 200, path
            assert "<title>app</title>" in response.text
