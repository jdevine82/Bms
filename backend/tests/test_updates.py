"""Updates in the web app (milestone 12b, decision 031): Setup → Updates on a site server with
the host's updater agent (a real agent with a stand-in for pct and the site's health), the
agent's report in the audit log, the push to the standby HMIs, and the Pi side (staging).

Needs BMS_TEST_DATABASE_URL."""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import stat
import threading
from pathlib import Path

import httpx
import pytest

from test_updater import TOKEN, U, FakeSite, release

if not os.environ.get("BMS_TEST_DATABASE_URL"):
    pytest.skip("BMS_TEST_DATABASE_URL is not set", allow_module_level=True)
pytestmark = pytest.mark.db

STANDBY_TOKEN = "standby-token-0123456789abcdef"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def wait_for(check, timeout=15.0, what="the condition"):
    end = asyncio.get_running_loop().time() + timeout
    while not await check():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.1)


class FakeHmi:
    """A standby HMI receiving the server's push (POST /api/standby/update)."""

    def __init__(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        got = self.got = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers["Content-Length"]))
                got.append((self.path, self.headers.get("X-BMS-Standby-Token"), body))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


async def server_stack(tmp_path, site: FakeSite):
    """A site server backend on a real port, the updater agent on another, and a standby HMI."""
    import uvicorn

    from devine_bms.api.app import create_app
    from devine_bms.runtime import Runtime
    from devine_bms.settings import Settings
    from devine_bms.storage.postgres import PostgresStorage
    from helpers import TEST_INTERVALS, example_data
    from setup_helpers import ADMIN, URL, fresh_database

    from devine_bms.config.sync import sync_config
    from devine_bms.config.validate import parse_config
    from devine_bms.db.session import make_sessionmaker

    engine = await fresh_database()
    sessions = make_sessionmaker(engine)
    config, _ = parse_config(example_data(1))
    async with sessions() as session, session.begin():
        await sync_config(session, config)
    port = _free_port()
    release_dir = tmp_path / "releases"
    release_dir.mkdir()
    hmi = FakeHmi()

    # The agent reports to the real backend; the site's health and pct are the stand-in.
    def http(method, url, body=None, token=None, timeout=5):
        if url.endswith("/api/updates/report"):
            return U.http_json(method, f"http://127.0.0.1:{port}/api/updates/report", body, token, timeout)
        return site.http(method, url, body, token, timeout)

    original_run = site.run

    def run(cmd, stdin=None, timeout=0):
        if cmd[:2] == ["pct", "push"]:  # the release lands in bms-app's release folder
            shutil.copy(cmd[3], release_dir / Path(cmd[4]).name)
        return original_run(cmd, stdin, timeout)

    clock = {"t": 0.0}
    updater = U.Updater(tmp_path / "agent", token=TOKEN, runner=run, http=http,
                        sleep=lambda s: clock.__setitem__("t", clock["t"] + s), clock=lambda: clock["t"],
                        answer_s=30, stable_s=10, poll_s=5)
    agent = U.make_server(updater, ("127.0.0.1", 0), TOKEN)
    threading.Thread(target=agent.serve_forever, daemon=True).start()
    settings = Settings(database_url=URL, updater_url=f"http://127.0.0.1:{agent.server_address[1]}",
                        updater_token=TOKEN, release_dir=str(release_dir), hmi_urls=hmi.url,
                        standby_token=STANDBY_TOKEN, heartbeat_s=60)

    async def factory():
        runtime = Runtime(PostgresStorage(sessions), "dev_site", intervals=TEST_INTERVALS)
        runtime.settings = settings

        async def cleanup():
            pass

        return runtime, cleanup

    app = create_app(factory)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
            assert (await client.post("/api/auth/first-admin", json=ADMIN)).status_code == 200
            yield {"client": client, "updater": updater, "hmi": hmi, "site": site, "port": port,
                   "sessions": sessions}
    finally:
        server.should_exit = True
        await task
        agent.shutdown()
        hmi.server.shutdown()
        await engine.dispose()


async def audit(client, action):
    return (await client.get(f"/api/audit?action={action}")).json()


async def test_an_upload_updates_the_server_and_pushes_to_the_hmis(tmp_path):
    async for s in server_stack(tmp_path, FakeSite()):
        c = s["client"]
        st = (await c.get("/api/updates")).json()
        assert st["kind"] == "server" and st["agent"]["state"] == "idle"
        assert st["hmis"][0]["url"] == s["hmi"].url

        bad = await c.post("/api/updates/upload", content=b"not a release")
        assert bad.status_code == 422 and "not a .tar.gz release" in bad.json()["error"]
        r = await c.post("/api/updates/upload", content=release(tmp_path).read_bytes())
        assert r.status_code == 200, r.text
        assert r.json()["version"] == "0.2.0"

        async def reported():
            return any("passed its health check" in e["summary"] for e in await audit(c, "update"))

        await wait_for(reported, what="the agent's report")
        assert (await c.get("/api/updates")).json()["agent"]["state"] == "passed"
        started = await audit(c, "update-start")
        assert started[0]["summary"] == "started the update from 0.1.0 to 0.2.0" and started[0]["user"] == "admin"

        async def pushed():
            return bool(s["hmi"].got) and any("update-hmi" == e["action"] for e in await audit(c, "update-hmi"))

        await wait_for(pushed, what="the push to the HMI")
        path, token, body = s["hmi"].got[0]
        assert path == "/api/standby/update" and token == STANDBY_TOKEN
        assert body == release(tmp_path).read_bytes()
        hmis = (await c.get("/api/updates")).json()["hmis"]
        assert hmis[0]["push"]["result"].startswith("accepted")


async def test_a_failed_update_is_reported_after_the_rollback(tmp_path):
    async for s in server_stack(tmp_path, FakeSite(install_rc=1)):
        c = s["client"]
        assert (await c.post("/api/updates/upload", content=release(tmp_path).read_bytes())).status_code == 200

        async def reported():
            return any("rolled back" in e["summary"] for e in await audit(c, "update"))

        await wait_for(reported, what="the rollback report")
        entry = (await audit(c, "update"))[0]
        assert "pip failed" in entry["summary"] and entry["after"]["steps"]
        assert not s["hmi"].got  # nothing is pushed after a failure


async def test_only_admins_and_only_the_agent(tmp_path):
    async for s in server_stack(tmp_path, FakeSite()):
        c = s["client"]
        r = await c.post("/api/users", json={"username": "op", "dis": "Op", "role": "operator",
                                             "password": "a long enough pw"})
        assert r.status_code == 200
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{s['port']}") as op:
            await op.post("/api/auth/login", json={"username": "op", "password": "a long enough pw"})
            assert (await op.get("/api/updates")).status_code == 403
            assert (await op.post("/api/updates/upload", content=b"x")).status_code == 403
            # The report needs the agent's token; the HMI endpoint the standby token (and a standby HMI).
            assert (await op.post("/api/updates/report", json={"state": "passed"})).status_code == 403
            assert (await op.post("/api/updates/report", json={"state": "passed"},
                                  headers={"X-BMS-Update-Token": "wrong"})).status_code == 403
            assert (await op.post("/api/standby/update", content=b"x")).status_code == 403
            assert (await op.post("/api/standby/update", content=b"x",
                                  headers={"X-BMS-Standby-Token": STANDBY_TOKEN})).status_code == 404


# ---------- a Pi ----------

def _stage_script(tmp_path: Path) -> Path:
    script = tmp_path / "stage.sh"
    script.write_text(f'#!/bin/bash\ncp "$1" {tmp_path}/staged.tar.gz\necho staged\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


class _Settings:
    def __init__(self, tmp_path, cmd):
        self.updater_url = self.updater_token = self.hmi_urls = ""
        self.update_dir = str(tmp_path / "updates")
        self.pi_update_cmd = cmd
        self.standby_token = STANDBY_TOKEN


async def _chunks(data: bytes):
    yield data[:100]
    yield data[100:]


async def test_a_pi_stages_the_release_and_refuses_in_direct_mode(tmp_path):
    from devine_bms.auth.service import Actor
    from devine_bms.updates.service import UpdateError, UpdateService

    records = []

    async def record(*a, **k):
        records.append((a, k))

    standby = type("S", (), {"mode": "standby"})()
    svc = UpdateService(_Settings(tmp_path, str(_stage_script(tmp_path))), record, "standby", lambda: standby)
    assert svc.kind == "pi"
    data = release(tmp_path).read_bytes()
    actor = Actor(client="127.0.0.1", role="admin", username="admin")
    out = await svc.upload(actor, _chunks(data))
    assert out == {"ok": True, "version": "0.2.0", "from": "0.1.0", "restarting": True}
    assert (tmp_path / "staged.tar.gz").read_bytes() == data
    assert "restarts twice" in records[-1][0][2]
    standby.mode = "direct"
    with pytest.raises(UpdateError, match="direct mode"):
        await svc.upload(actor, _chunks(data))
    import tempfile

    assert not list(Path(tempfile.gettempdir()).glob("upload-*.tar.gz"))  # nothing left behind
    none = UpdateService(_Settings(tmp_path, ""), record, "full")
    assert none.kind == "none"
    with pytest.raises(UpdateError, match="not set up"):
        await none.upload(actor, _chunks(data))


def test_the_backend_and_the_agent_check_releases_the_same_way(tmp_path):
    from devine_bms.updates.release import ReleaseError, check_release

    cases = [{}, {"drop": "VERSION"}, {"drop": "web/dist/index.html"}, {"top": "x"},
             {"extra": {"../evil": "x"}}, {"extra": {"VERSION": "1 2"}}]
    for i, kwargs in enumerate(cases):
        d = tmp_path / str(i)
        d.mkdir()
        path = release(d, **kwargs)
        results = []
        for check, error in ((check_release, ReleaseError), (U.check_release, U.ReleaseError)):
            try:
                results.append(check(path))
            except error as exc:
                results.append(f"refused: {exc}")
        assert results[0] == results[1], (kwargs, results)
