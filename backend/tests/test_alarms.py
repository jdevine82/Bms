"""Alarms (main spec 5.5 and 5.5.1; milestone 6B): definitions and checks, the engine, acknowledge
and comments, delivery by email, SMS and ntfy with quiet hours and retries, and roles.

Needs BMS_TEST_DATABASE_URL (see test_db_sync.py).
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import update

from devine_bms.alarms.delivery import in_quiet_hours
from devine_bms.alarms.engine import AlarmEngine, Definition
from devine_bms.config.model import PointAlarm
from devine_bms.db import models as db
from devine_bms.values import LiveValue, Status
from fake_servers import FakeHttp, FakeSmtp
from setup_helpers import stack  # noqa: F401 (stack is a fixture)

if not os.environ.get("BMS_TEST_DATABASE_URL"):
    pytest.skip("BMS_TEST_DATABASE_URL is not set", allow_module_level=True)
pytestmark = pytest.mark.db


async def wait_for(check, timeout=8.0):
    end = asyncio.get_running_loop().time() + timeout
    while True:
        result = await check()
        if result:
            return result
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("timed out")
        await asyncio.sleep(0.1)


async def set_alarms(client, point, alarms):
    p = (await client.get(f"/api/setup/points/{point}")).json()
    p["alarms"] = alarms
    r = await client.put(f"/api/setup/points/{point}", json=p)
    assert r.status_code == 200, r.text
    return r


async def open_alarms(client):
    return (await client.get("/api/alarms")).json()


# ---------- the conditions, without a database ----------

def engine_with(points, values):
    cache = SimpleNamespace(get=lambda pid: values.get(pid))
    runtime = SimpleNamespace(cache=cache, config=SimpleNamespace(points=points, devices=[]))
    return AlarmEngine(runtime, None, "x")


def live(pid, value, status=Status.OK):
    return LiveValue(pid, value, status, datetime.now(UTC))


def test_conditions_deadband_quality_state_and_offline():
    pid, dev = uuid.uuid4(), uuid.uuid4()
    high = Definition("k", "high", "warning", 0, "m", "p", "P", point_id=pid,
                      alarm=PointAlarm(type="high", level="warning", limit=50, deadband=2))
    e = engine_with([], {pid: live(pid, 51.0)})
    assert e._condition(high, active=False)[0] is True
    e = engine_with([], {pid: live(pid, 49.0)})
    assert e._condition(high, active=False)[0] is False
    assert e._condition(high, active=True)[0] is True  # still inside the deadband: stays raised
    e = engine_with([], {pid: live(pid, 47.0)})
    assert e._condition(high, active=True)[0] is False
    e = engine_with([], {pid: live(pid, 99.0, Status.DOWN)})
    assert e._condition(high, active=False)[0] is None  # a Bad value neither raises nor clears

    state = Definition("k", "state", "info", 0, "m", "p", "P", point_id=pid,
                       alarm=PointAlarm(type="state", level="info", state="Fault"))
    assert engine_with([], {pid: live(pid, "fault")})._condition(state, False)[0] is True
    stale = Definition("k", "stale", "info", 0, "m", "p", "P", point_id=pid,
                       alarm=PointAlarm(type="stale", level="info"))
    assert engine_with([], {pid: live(pid, 1.0, Status.STALE)})._condition(stale, False)[0] is True

    p1, p2 = SimpleNamespace(id=uuid.uuid4(), device_id=dev), SimpleNamespace(id=uuid.uuid4(), device_id=dev)
    offline = Definition("k", "offline", "warning", 0, "m", "d", "D", device_id=dev)
    down = engine_with([p1, p2], {p1.id: live(p1.id, None, Status.DOWN), p2.id: live(p2.id, None, Status.DOWN)})
    assert down._condition(offline, False)[0] is True
    half = engine_with([p1, p2], {p1.id: live(p1.id, None, Status.DOWN), p2.id: live(p2.id, 1.0)})
    assert half._condition(offline, False)[0] is False


def test_quiet_hours_window():
    tz = datetime(2026, 11, 6, 23, 0).astimezone().tzinfo
    late = datetime(2026, 11, 6, 23, 30, tzinfo=tz)
    end = in_quiet_hours({"start": "22:00", "end": "06:00"}, late)
    assert end == datetime(2026, 11, 7, 6, 0, tzinfo=tz)
    assert in_quiet_hours({"start": "22:00", "end": "06:00"}, late.replace(hour=12)) is None
    assert in_quiet_hours({"start": "09:00", "end": "17:00"}, late.replace(hour=10)) is not None


# ---------- checks on the definitions ----------

async def test_alarm_definitions_are_checked(stack):
    client = stack["client"]
    p = (await client.get("/api/setup/points/site_meter1_power")).json()
    for alarms, words in (([{"type": "state", "level": "info", "state": True}], "Bool or Str"),
                          ([{"type": "high", "level": "info"}], "needs a limit"),
                          ([{"type": "high", "level": "info", "limit": 5}, {"type": "low", "level": "info", "limit": 9}],
                           "above the low"),
                          ([{"type": "high", "level": "info", "limit": 5}, {"type": "high", "level": "info", "limit": 6}],
                           "one alarm of each type")):
        r = await client.put("/api/setup/points/site_meter1_power", json={**p, "alarms": alarms}, params={"check": "true"})
        assert r.status_code == 422, alarms
        text = r.text
        assert words in text, (words, text)
    ok = await client.put("/api/setup/points/site_meter1_power?check=true",
                          json={**p, "alarms": [{"type": "high", "level": "critical", "limit": 50, "deadband": 2,
                                                 "delayS": 30, "message": "Main power high"}]})
    assert ok.json()["ok"]
    exported = (await client.get("/api/setup/config")).json()
    assert all("alarms" not in pt for pt in exported["points"])  # no alarms: nothing in the file


# ---------- raise, notify, acknowledge, return ----------

async def delivery_setup(client, http, smtp, **extra):
    settings = {
        "email": {"host": "127.0.0.1", "port": smtp.port, "security": "none", "from": "bms@example.com",
                  "username": ""},
        "sms": {"username": "acct", "apiKeyEnv": "BMS_TEST_CLICKSEND", "baseUrl": http.url},
        "ntfy": {"server": http.url},
        "recipients": [{"id": "r1", "name": "Jason", "email": "jason@example.com", "phone": "+61400000001",
                        "ntfyTopic": "devine-test", "levels": {"info": False, "warning": True, "critical": True},
                        **extra}],
    }
    r = await client.put("/api/alarm-settings", json=settings)
    assert r.status_code == 200, r.text
    return r.json()


async def test_raise_notify_acknowledge_and_return(stack, monkeypatch):
    monkeypatch.setenv("BMS_TEST_CLICKSEND", "secret-key")
    client = stack["client"]
    http, smtp = FakeHttp(), FakeSmtp()
    await http.start()
    await smtp.start()
    try:
        saved = await delivery_setup(client, http, smtp)
        assert saved["warnings"] == []
        await set_alarms(client, "site_meter1_power", [{"type": "high", "level": "critical", "limit": 1,
                                                        "message": "Main power high"}])
        alarms = await wait_for(lambda: open_alarms(client))
        a = alarms[0]
        assert a["active"] and a["level"] == "critical" and a["message"] == "Main power high"
        assert a["detail"]["limit"] == 1 and a["detail"]["unit"] == "kW"

        async def all_sent():
            rows = (await client.get("/api/alarm-deliveries")).json()
            return rows if len(rows) == 3 and all(r["status"] == "sent" for r in rows) else None
        rows = await wait_for(all_sent)
        assert {r["channel"] for r in rows} == {"email", "sms", "ntfy"}
        assert "Main power high" in smtp.messages[0]["data"] and smtp.messages[0]["to"] == ["jason@example.com"]
        sms = next(r for r in http.requests if r["path"] == "/v3/sms/send")
        assert sms["json"]["messages"][0]["to"] == "+61400000001" and sms["headers"]["authorization"].startswith("Basic")
        push = next(r for r in http.requests if r["path"] == "/")
        assert push["json"]["topic"] == "devine-test" and push["json"]["priority"] == 5
        assert "[Critical]" in push["json"]["title"]

        summary = (await client.get("/api/alarms/summary")).json()
        assert summary["unacked"]["critical"] == 1 and summary["active"] == 1
        acked = await client.post("/api/alarms/ack", json={"ids": [a["id"]], "comment": "electrician called"})
        assert acked.json()["acknowledged"][0]["ackedBy"] == "admin"
        assert (await open_alarms(client))[0]["ackedAt"]  # still open: still active

        await set_alarms(client, "site_meter1_power", [{"type": "high", "level": "critical", "limit": 1000}])

        async def closed():
            return not await open_alarms(client)
        await wait_for(closed)
        detail = (await client.get(f"/api/alarms/{a['id']}")).json()
        assert [e["kind"] for e in detail["events"]] == ["raised", "acked", "returned"]
        assert detail["events"][1]["text"] == "electrician called" and detail["closed"]
        assert len(detail["deliveries"]) == 3
        log = (await client.get("/api/audit?action=alarm-ack")).json()
        assert log and "electrician called" in log[0]["summary"]
    finally:
        await http.stop()
        await smtp.stop()


async def test_delay_and_reraise_without_new_notification(stack):
    client = stack["client"]
    await set_alarms(client, "site_meter1_power", [{"type": "high", "level": "info", "limit": 1, "delayS": 1.5}])
    await asyncio.sleep(0.7)
    assert await open_alarms(client) == []  # the delay has not passed
    a = (await wait_for(lambda: open_alarms(client)))[0]
    await set_alarms(client, "site_meter1_power", [{"type": "high", "level": "info", "limit": 1000}])
    await wait_for(lambda: _inactive(client))
    await set_alarms(client, "site_meter1_power", [{"type": "high", "level": "info", "limit": 1}])

    async def active_again():
        rows = await open_alarms(client)
        return rows if rows and rows[0]["active"] else None
    again = (await wait_for(active_again))[0]
    assert again["id"] == a["id"]  # the same occurrence: it was never acknowledged


async def _inactive(client):
    rows = await open_alarms(client)
    return rows and not rows[0]["active"]


async def test_removed_definition_returns_the_alarm(stack):
    client = stack["client"]
    await set_alarms(client, "site_meter1_power", [{"type": "high", "level": "warning", "limit": 1}])
    a = (await wait_for(lambda: open_alarms(client)))[0]
    await set_alarms(client, "site_meter1_power", [])
    await wait_for(lambda: _inactive(client))
    events = (await client.get(f"/api/alarms/{a['id']}")).json()["events"]
    assert "removed from the setup" in events[-1]["text"]


async def test_device_offline_alarm(stack):
    client, sim = stack["client"], stack["sim"]
    d = (await client.get("/api/setup/devices/sim_meter")).json()
    d["offlineAlarm"] = {"level": "warning", "delayS": 0}
    assert (await client.put("/api/setup/devices/sim_meter", json=d)).status_code == 200
    await sim.stop()

    async def offline():
        rows = await open_alarms(client)
        return [r for r in rows if r["type"] == "offline" and r["active"]]
    a = (await wait_for(offline, timeout=20))[0]
    assert a["message"] == "Simulated power meter is not answering" and a["source"] == "sim_meter"


async def test_quiet_hours_hold_warnings_and_send_one_summary(stack, monkeypatch):
    client = stack["client"]
    http, smtp = FakeHttp(), FakeSmtp()
    await http.start()
    await smtp.start()
    try:
        now = datetime.now(ZoneInfo("Australia/Sydney"))  # quiet hours are in the site's time zone
        quiet = {"start": (now - timedelta(hours=1)).strftime("%H:%M"),
                 "end": (now + timedelta(hours=1)).strftime("%H:%M")}
        await delivery_setup(client, http, smtp, quietHours=quiet)
        await set_alarms(client, "site_meter1_power", [{"type": "high", "level": "warning", "limit": 1}])
        await set_alarms(client, "site_meter1_voltage", [{"type": "high", "level": "warning", "limit": 1}])

        async def two_held():
            rows = (await client.get("/api/alarm-deliveries?status=held")).json()
            return rows if len(rows) == 4 else None  # 2 alarms x (email, ntfy); SMS is Critical only
        await wait_for(two_held)
        assert smtp.messages == []
        async with stack["sessions"]() as session:
            await session.execute(update(db.AlarmDelivery).values(hold_until=datetime.now(UTC) - timedelta(seconds=1)))
            await session.commit()

        async def summary_sent():
            rows = (await client.get("/api/alarm-deliveries?status=sent")).json()
            return rows if len(rows) == 4 else None
        await wait_for(summary_sent, timeout=12)
        assert len(smtp.messages) == 1 and "2 warnings during quiet hours" in smtp.messages[0]["data"]
    finally:
        await http.stop()
        await smtp.stop()


async def test_failed_sends_retry(stack):
    client = stack["client"]
    http, smtp = FakeHttp(), FakeSmtp()
    await http.start()
    await smtp.start()
    try:
        await delivery_setup(client, http, smtp)
        settings = (await client.get("/api/alarm-settings")).json()["settings"]
        settings["channels"]["warning"] = ["ntfy"]
        await client.put("/api/alarm-settings", json=settings)
        http.status = 500
        await set_alarms(client, "site_meter1_power", [{"type": "high", "level": "warning", "limit": 1}])

        async def retrying():
            rows = (await client.get("/api/alarm-deliveries")).json()
            return rows if rows and rows[0]["error"] and rows[0]["status"] == "pending" else None
        row = (await wait_for(retrying))[0]
        assert "ntfy answered 500" in row["error"]
        http.status = 200
        async with stack["sessions"]() as session:
            await session.execute(update(db.AlarmDelivery).values(next_attempt_at=datetime.now(UTC)))
            await session.commit()

        async def sent():
            rows = (await client.get("/api/alarm-deliveries")).json()
            return rows if rows[0]["status"] == "sent" else None
        assert (await wait_for(sent, timeout=12))[0]["attempts"] == 2
    finally:
        await http.stop()
        await smtp.stop()


async def test_settings_checks_and_test_message(stack, monkeypatch):
    client = stack["client"]
    bad = await client.put("/api/alarm-settings", json={"sms": {"apiKeyEnv": "abc123-real-key"}})
    assert bad.status_code == 422 and "never put the secret" in bad.json()["error"]
    nobody = await client.put("/api/alarm-settings", json={"recipients": []})
    assert any("Nobody receives Critical" in w for w in nobody.json()["warnings"])
    http, smtp = FakeHttp(), FakeSmtp()
    await http.start()
    await smtp.start()
    try:
        await delivery_setup(client, http, smtp)
        monkeypatch.delenv("BMS_TEST_CLICKSEND", raising=False)
        failed = (await client.post("/api/alarm-settings/test", json={"recipient": "r1", "channel": "sms"})).json()
        assert failed["ok"] is False and "BMS_TEST_CLICKSEND is not set" in failed["error"]
        ok = (await client.post("/api/alarm-settings/test", json={"recipient": "r1", "channel": "email"})).json()
        assert ok == {"ok": True, "error": None} and "test message" in smtp.messages[0]["data"]
        log = (await client.get("/api/alarm-deliveries")).json()
        assert {(r["channel"], r["status"], r["test"]) for r in log} == {("sms", "failed", True), ("email", "sent", True)}
    finally:
        await http.stop()
        await smtp.stop()


async def test_roles_for_alarms(stack):
    admin = stack["client"]
    await admin.post("/api/users", json={"username": "vic", "dis": "Vic", "role": "viewer", "password": "a long enough pw"})
    await admin.post("/api/users", json={"username": "ola", "dis": "Ola", "role": "operator", "password": "a long enough pw"})
    await set_alarms(admin, "site_meter1_power", [{"type": "high", "level": "warning", "limit": 1}])
    a = (await wait_for(lambda: open_alarms(admin)))[0]
    transport = lambda ip: httpx.ASGITransport(app=stack["app"], client=(ip, 1))  # noqa: E731
    async with httpx.AsyncClient(transport=transport("10.0.0.7"), base_url="http://bms") as viewer, \
            httpx.AsyncClient(transport=transport("10.0.0.8"), base_url="http://bms") as operator:
        await viewer.post("/api/auth/login", json={"username": "vic", "password": "a long enough pw"})
        await operator.post("/api/auth/login", json={"username": "ola", "password": "a long enough pw"})
        assert len((await viewer.get("/api/alarms")).json()) == 1
        assert (await viewer.post("/api/alarms/ack", json={"ids": [a["id"]]})).status_code == 403
        assert (await viewer.get("/api/alarm-settings")).status_code == 403
        assert (await operator.post(f"/api/alarms/{a['id']}/comment", json={"text": "looking"})).status_code == 200
        assert (await operator.post("/api/alarms/ack", json={"ids": [a["id"]]})).json()["acknowledged"]
        assert (await operator.get("/api/alarm-settings")).status_code == 403


async def test_template_roles_carry_alarm_defaults(stack):
    client = stack["client"]
    template = {"id": "my-meter", "version": "1.0.0", "dis": "Meter", "tags": {"elec": True, "meter": True},
                "roles": [{"role": "power", "dis": "Power", "type": "sensor", "kind": "Number", "unit": "kW",
                           "tags": {"elec": True, "power": True},
                           "alarms": [{"type": "high", "level": "warning", "limit": 80}]}]}
    assert (await client.post("/api/setup/equip-templates", json=template)).json()["applied"]
    made = await client.post("/api/setup/equip-templates/my-meter/create", json={
        "name": "meter7", "dis": "Meter 7", "bindings": {"power": {"device": "sim_meter", "address": {
            "objectType": "holdingRegister", "address": 0, "dataType": "float32"}}}})
    assert made.json()["applied"]
    point = (await client.get("/api/setup/points/site_meter7_power")).json()
    assert point["alarms"][0]["limit"] == 80
    point["alarms"][0]["limit"] = 95  # an override of the template's alarm
    await client.put("/api/setup/points/site_meter7_power", json=point)
    instance = (await client.get("/api/setup/instances/equips/meter7")).json()
    assert "alarms" in instance["roles"][0]["overrides"]
