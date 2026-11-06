"""Logins, roles, sessions, 2FA and the audit log (main spec 5.6 and 7; milestone 6A).

Needs BMS_TEST_DATABASE_URL (see test_db_sync.py).
"""

import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update

from devine_bms.auth import totp
from devine_bms.auth.service import AuthService
from devine_bms.db import models as db
from helpers import example_data
from setup_helpers import ADMIN, running_stack, stack  # noqa: F401 (stack is a fixture)

if not os.environ.get("BMS_TEST_DATABASE_URL"):
    pytest.skip("BMS_TEST_DATABASE_URL is not set", allow_module_level=True)
pytestmark = pytest.mark.db


def client_from(app, address: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(address, 40000)), base_url="http://bms")


async def login(client, username, password, code=None):
    return await client.post("/api/auth/login", json={"username": username, "password": password, "code": code})


async def add_user(admin, username, role, password="a long enough pw"):
    r = await admin.post("/api/users", json={"username": username, "dis": username.title(), "role": role,
                                             "password": password})
    assert r.status_code == 200, r.text


async def test_first_run_only_from_the_server_and_only_once():
    async for s in running_stack(example_data(5099)):
        app = s["app"]
        async with client_from(app, "192.168.1.9") as lan:
            state = (await lan.get("/api/auth/state")).json()
            assert state["firstRun"] is False  # the fixture's client already made the first admin
            assert (await lan.post("/api/auth/first-admin", json=ADMIN)).status_code == 403
        async with client_from(app, "127.0.0.1") as local:
            again = await local.post("/api/auth/first-admin", json={**ADMIN, "username": "second"})
            assert again.status_code == 409


async def test_first_run_page_state(stack):
    sessions = stack["sessions"]
    async with sessions() as session:
        await session.execute(update(db.AuditEntry).values(user_id=None))
        await session.execute(db.User.__table__.delete())
        await session.commit()
    async with client_from(stack["app"], "10.0.0.5") as lan:
        state = (await lan.get("/api/auth/state")).json()
        assert state == {"firstRun": True, "firstRunHere": False, "user": None}
        assert (await lan.post("/api/auth/first-admin", json=ADMIN)).status_code == 403
    async with client_from(stack["app"], "127.0.0.1") as local:
        assert (await local.get("/api/auth/state")).json()["firstRunHere"] is True
        short = await local.post("/api/auth/first-admin", json={**ADMIN, "password": "short"})
        assert short.status_code == 422 and "10 characters" in short.json()["error"]
        made = await local.post("/api/auth/first-admin", json=ADMIN)
        assert made.json()["user"]["role"] == "admin"
        assert (await local.get("/api/auth/me")).json()["username"] == "admin"


async def test_every_route_needs_a_login(stack):
    async with client_from(stack["app"], "192.168.1.20") as anon:
        assert (await anon.get("/api/health")).status_code == 200
        for path in ("/api/site", "/api/points", "/api/setup/config", "/api/users", "/api/audit", "/api/auth/me"):
            r = await anon.get(path)
            assert r.status_code == 401 and r.json()["code"] == "login", path


async def test_roles(stack):
    admin = stack["client"]
    await add_user(admin, "olivia", "operator")
    await add_user(admin, "victor", "viewer")
    async with client_from(stack["app"], "192.168.1.21") as op, client_from(stack["app"], "192.168.1.22") as view:
        assert (await login(op, "Olivia", "a long enough pw")).status_code == 200  # names ignore case
        assert (await login(view, "victor", "a long enough pw")).status_code == 200
        for c in (op, view):
            assert (await c.get("/api/points")).status_code == 200
            assert (await c.get("/api/setup/config")).status_code == 403
            assert (await c.get("/api/users")).status_code == 403
            assert (await c.post("/api/setup/test-read", json={"device": "sim_meter"})).status_code == 403
        own = (await op.get("/api/audit")).json()
        assert own and {e["user"] for e in own} == {"olivia"}  # operators see their own actions
        assert (await view.get("/api/audit")).status_code == 403
    everyone = (await admin.get("/api/audit?action=login")).json()
    assert {e["user"] for e in everyone} >= {"olivia", "victor"}


async def test_lockout_and_unlock(stack):
    admin = stack["client"]
    await add_user(admin, "lee", "operator")
    async with client_from(stack["app"], "192.168.1.30") as c:
        for _ in range(5):
            assert (await login(c, "lee", "wrong password!")).status_code == 401
        locked = await login(c, "lee", "a long enough pw")
        assert locked.status_code == 423 and locked.json()["code"] == "locked"
        users = {u["username"]: u for u in (await admin.get("/api/users")).json()}
        assert users["lee"]["locked"] is True
        assert (await admin.post("/api/users/lee/unlock")).status_code == 200
        assert (await login(c, "lee", "a long enough pw")).status_code == 200
    log = (await admin.get("/api/audit?user=lee&action=login-failed")).json()
    assert any("account locked for 10 minutes" in e["summary"] for e in log)
    assert len(log) >= 6


async def test_rate_limit_per_address(stack):
    async with client_from(stack["app"], "192.168.1.31") as c:
        codes = [(await login(c, "nobody", "whatever pw!")).status_code for _ in range(11)]
        assert codes[:10] == [401] * 10 and codes[10] == 429
    async with client_from(stack["app"], "192.168.1.32") as other:  # another address is not affected
        assert (await login(other, "nobody", "whatever pw!")).status_code == 401


async def test_sessions_idle_timeout_logout_and_password_change(stack):
    admin = stack["client"]
    await add_user(admin, "sam", "viewer")
    async with client_from(stack["app"], "192.168.1.40") as a, client_from(stack["app"], "192.168.1.41") as b:
        await login(a, "sam", "a long enough pw")
        await login(b, "sam", "a long enough pw")
        assert (await a.get("/api/points")).status_code == 200
        # Idle for longer than the time-out: the session ends.
        async with stack["sessions"]() as session:
            await session.execute(update(db.UserSession).where(db.UserSession.client == "192.168.1.40")
                                  .values(last_seen=datetime.now(UTC) - timedelta(minutes=31)))
            await session.commit()
        assert (await a.get("/api/points")).status_code == 401
        # A password change signs out the user's other browsers.
        await login(a, "sam", "a long enough pw")
        changed = await a.post("/api/auth/me/password", json={"current": "a long enough pw",
                                                              "password": "another long one"})
        assert changed.status_code == 200
        assert (await b.get("/api/points")).status_code == 401
        assert (await a.get("/api/points")).status_code == 200
        await a.post("/api/auth/logout", json={})
        assert (await a.get("/api/points")).status_code == 401


async def test_two_factor(stack):
    admin = stack["client"]
    begun = (await admin.post("/api/auth/me/totp")).json()
    assert begun["uri"].startswith("otpauth://totp/") and begun["secret"]
    assert (await admin.post("/api/auth/me/totp/confirm", json={"code": "000000"})).status_code == 422
    assert (await admin.post("/api/auth/me/totp/confirm",
                             json={"code": totp.code_at(begun["secret"])})).status_code == 200
    async with client_from(stack["app"], "192.168.1.50") as c:
        need = await login(c, "admin", ADMIN["password"])
        assert need.status_code == 401 and need.json()["code"] == "totp-required"
        assert (await login(c, "admin", ADMIN["password"], "123456")).status_code == 401
        ok = await login(c, "admin", ADMIN["password"], totp.code_at(begun["secret"]))
        assert ok.status_code == 200 and ok.json()["user"]["totp"] is True


async def test_security_settings_kiosk_and_allowed_networks(stack):
    admin = stack["client"]
    bad = await admin.put("/api/auth/settings", json={"minPasswordLength": 6})
    assert bad.status_code == 422
    assert (await admin.put("/api/auth/settings", json={"kioskAddresses": ["192.168.1.60"]})).status_code == 200
    async with client_from(stack["app"], "192.168.1.60") as kiosk:
        assert (await kiosk.get("/api/points")).status_code == 200  # viewer access, no login
        assert (await kiosk.get("/api/setup/config")).status_code == 403
        assert (await kiosk.get("/api/auth/me")).json()["kiosk"] is True
    ok = await admin.put("/api/auth/settings", json={"allowedNetworks": ["10.20.0.0/16"]})
    assert ok.status_code == 200  # the admin is on this machine, which is always allowed
    async with client_from(stack["app"], "192.168.1.70") as outside:
        assert (await login(outside, "admin", ADMIN["password"])).status_code == 403
    async with client_from(stack["app"], "10.20.3.4") as inside:
        assert (await login(inside, "admin", ADMIN["password"])).status_code == 200
        lock_out = await inside.put("/api/auth/settings", json={"allowedNetworks": ["10.99.0.0/16"]})
        assert lock_out.status_code == 422 and "lock you out" in lock_out.json()["error"]


async def test_tailscale_proxy_address_is_the_client(stack):
    """tailscale serve passes requests on from this machine with X-Forwarded-For (main spec 8)."""
    async with client_from(stack["app"], "127.0.0.1") as proxied:
        headers = {"X-Forwarded-For": "100.64.1.2"}
        state = (await proxied.get("/api/auth/state", headers=headers)).json()
        assert state["firstRunHere"] is False
        await proxied.post("/api/auth/login", json={"username": "admin", "password": "nope nope nope"}, headers=headers)
    log = (await stack["client"].get("/api/audit?action=login-failed")).json()
    assert log[0]["client"] == "100.64.1.2"


async def test_users_admin_rules(stack):
    admin = stack["client"]
    assert (await admin.put("/api/users/admin", json={"role": "operator"})).status_code == 409  # last admin
    assert (await admin.delete("/api/users/admin")).status_code == 409  # own account
    await add_user(admin, "pat", "operator")
    dup = await admin.post("/api/users", json={"username": "pat", "dis": "Pat", "role": "viewer",
                                               "password": "a long enough pw"})
    assert dup.status_code == 409
    assert (await admin.put("/api/users/pat", json={"enabled": False})).status_code == 200
    async with client_from(stack["app"], "192.168.1.80") as c:
        assert (await login(c, "pat", "a long enough pw")).status_code == 401
    assert (await admin.delete("/api/users/pat")).status_code == 200
    removed = (await admin.get("/api/audit?action=user-remove")).json()[0]
    assert removed["name"] == "pat" and removed["user"] == "admin"


async def test_audit_csv_and_retention(stack):
    admin = stack["client"]
    csv = await admin.get("/api/audit.csv")
    assert csv.status_code == 200 and csv.text.startswith("time_utc,user,client,action")
    async with stack["sessions"]() as session:
        site = await session.scalar(select(db.Site))
        session.add(db.AuditEntry(site_id=site.id, client="old", action="put", summary="ancient",
                                  ts=datetime.now(UTC) - timedelta(days=400)))
        await session.commit()
    removed = await AuthService(stack["sessions"], "dev_site").prune()
    assert removed == 1
    assert not any(e["summary"] == "ancient" for e in (await admin.get("/api/audit?limit=5000")).json())
