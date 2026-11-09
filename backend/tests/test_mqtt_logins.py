"""Device logins on the site's own MQTT broker (Setup -> Buses and networks -> Device logins):
the root helper deploy/containers/bms-mqtt-users with Mosquitto's own tools, a real broker
accepting and then refusing a device, and the admin API. The API test needs BMS_TEST_DATABASE_URL."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / "deploy" / "containers" / "bms-mqtt-users"
MOSQUITTO = shutil.which("mosquitto") or "/usr/sbin/mosquitto"
pytestmark = pytest.mark.skipif(not shutil.which("mosquitto_passwd"), reason="Mosquitto is not installed")


def helper(passwd: Path, *args: str, stdin: str = "", reload: str = "") -> subprocess.CompletedProcess:
    env = {**os.environ, "BMS_MQTT_PASSWD_FILE": str(passwd), "BMS_MQTT_RELOAD_CMD": reload,
           "BMS_MQTT_GROUP": "no-such-group"}
    return subprocess.run([sys.executable, str(HELPER), *args], input=stdin, capture_output=True, text=True, env=env)


def test_add_list_reset_remove_and_the_bms_login_is_kept(tmp_path):
    pw = tmp_path / "passwd"
    assert helper(pw, "set-bms", stdin="bms broker password 123\n").returncode == 0
    assert helper(pw, "list").stdout.strip() == "[]"  # the BMS's own login is not listed
    r = helper(pw, "add", "plug1", stdin="a strong device password\n")
    assert r.returncode == 0, r.stderr
    assert json.loads(helper(pw, "list").stdout) == ["plug1"]
    first = pw.read_text()
    assert "a strong device password" not in first  # hashed by Mosquitto
    assert first.count("plug1:$7$") == 1 and first.count("bms:$7$") == 1
    assert oct(pw.stat().st_mode & 0o777) == "0o640"
    # A new password replaces the line; the BMS login stays; set-bms again keeps device logins.
    assert helper(pw, "add", "plug1", stdin="another strong password\n").returncode == 0
    assert pw.read_text().count("plug1:") == 1 and pw.read_text() != first
    assert helper(pw, "set-bms", stdin="bms broker password 456\n").returncode == 0
    assert json.loads(helper(pw, "list").stdout) == ["plug1"]
    assert helper(pw, "remove", "plug1").returncode == 0
    assert helper(pw, "list").stdout.strip() == "[]" and "bms:" in pw.read_text()


@pytest.mark.parametrize("args, stdin, code, message", [
    (("add", "bms"), "a strong device password", 2, "own login"),
    (("add", "bad name"), "a strong device password", 2, "letters, digits"),
    (("add", "../x"), "a strong device password", 2, "letters, digits"),
    (("add", "plug1"), "short", 1, "at least 16"),
    (("add", "plug1"), "has:colon in the password", 1, "cannot contain"),
    (("remove", "nobody"), "", 3, "no login"),
    (("add", "a", "b"), "", 2, "usage"),
    (("drop",), "", 2, "usage"),
])
def test_refusals(tmp_path, args, stdin, code, message):
    r = helper(tmp_path / "passwd", *args, stdin=stdin)
    assert r.returncode == code and message in r.stderr, r.stderr


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _can_connect(port: int, user: str, password: str) -> bool:
    import aiomqtt

    try:
        async with aiomqtt.Client("127.0.0.1", port, username=user, password=password, timeout=3):
            return True
    except aiomqtt.MqttError:
        return False


async def test_a_real_broker_accepts_the_device_and_refuses_it_once_removed():
    folder = Path(tempfile.mkdtemp(prefix="bms-mqtt-logins-"))
    pw = folder / "passwd"
    port = _free_port()
    conf = [f"listener {port} 127.0.0.1", "persistence false", "allow_anonymous false", f"password_file {pw}"]
    if os.geteuid() == 0:
        conf.append("user root")
    (folder / "mosquitto.conf").write_text("\n".join(conf) + "\n")
    assert helper(pw, "set-bms", stdin="bms broker password 123\n").returncode == 0
    proc = subprocess.Popen([MOSQUITTO, "-c", str(folder / "mosquitto.conf")], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    reload = f"kill -HUP {proc.pid}"
    try:
        for _ in range(100):
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            await asyncio.sleep(0.05)
        assert not await _can_connect(port, "plug1", "a strong device password")
        assert helper(pw, "add", "plug1", stdin="a strong device password\n", reload=reload).returncode == 0
        await asyncio.sleep(0.5)  # Mosquitto reloads the file on SIGHUP
        assert await _can_connect(port, "plug1", "a strong device password")
        assert not await _can_connect(port, "plug1", "the wrong password!!")
        assert await _can_connect(port, "bms", "bms broker password 123")  # the BMS still connects
        assert helper(pw, "remove", "plug1", reload=reload).returncode == 0
        await asyncio.sleep(0.5)
        assert not await _can_connect(port, "plug1", "a strong device password")
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(5)
        shutil.rmtree(folder, ignore_errors=True)


@pytest.mark.db
async def test_the_admin_api(tmp_path):
    if not os.environ.get("BMS_TEST_DATABASE_URL"):
        pytest.skip("BMS_TEST_DATABASE_URL is not set")
    import httpx

    from devine_bms.api.app import create_app
    from devine_bms.config.sync import sync_config
    from devine_bms.config.validate import parse_config
    from devine_bms.db.session import make_sessionmaker
    from devine_bms.runtime import Runtime
    from devine_bms.settings import Settings
    from devine_bms.storage.postgres import PostgresStorage
    from helpers import TEST_INTERVALS, example_data
    from setup_helpers import ADMIN, URL, fresh_database

    pw = tmp_path / "passwd"
    helper(pw, "set-bms", stdin="bms broker password 123\n")
    cmd = (f"env BMS_MQTT_PASSWD_FILE={pw} BMS_MQTT_RELOAD_CMD= BMS_MQTT_GROUP=no-such-group "
           f"{sys.executable} {HELPER}")
    engine = await fresh_database()
    sessions = make_sessionmaker(engine)
    config, _ = parse_config(example_data(1))
    async with sessions() as session, session.begin():
        await sync_config(session, config)

    def app_with(command: str):
        async def factory():
            runtime = Runtime(PostgresStorage(sessions), "dev_site", intervals=TEST_INTERVALS)
            runtime.settings = Settings(database_url=URL, mqtt_users_cmd=command)

            async def cleanup():
                pass

            return runtime, cleanup

        return create_app(factory)

    try:
        app = app_with(cmd)
        async with app.router.lifespan_context(app), httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 5000)), base_url="http://bms") as c:
            assert (await c.post("/api/auth/first-admin", json=ADMIN)).status_code == 200
            assert (await c.get("/api/mqtt-logins")).json() == {"available": True, "logins": []}
            r = await c.post("/api/mqtt-logins", json={"name": "plug1"})
            assert r.status_code == 200, r.text
            first = r.json()["password"]
            assert len(first) >= 30 and first not in pw.read_text()
            assert (await c.get("/api/mqtt-logins")).json()["logins"] == ["plug1"]
            assert (await c.post("/api/mqtt-logins", json={"name": "plug1"})).status_code == 409
            assert (await c.post("/api/mqtt-logins", json={"name": "bms"})).status_code == 422
            assert (await c.post("/api/mqtt-logins", json={"name": "a b"})).status_code == 422
            r = await c.post("/api/mqtt-logins/plug1/reset")
            assert r.status_code == 200 and r.json()["password"] != first
            assert (await c.post("/api/mqtt-logins/nobody/reset")).status_code == 404
            assert (await c.delete("/api/mqtt-logins/plug1")).status_code == 200
            assert (await c.delete("/api/mqtt-logins/plug1")).status_code == 404
            audit = (await c.get("/api/audit?action=mqtt-login")).json()
            assert [e["summary"] for e in audit][::-1] == [
                "added the MQTT device login plug1", "gave the MQTT device login plug1 a new password",
                "removed the MQTT device login plug1"]
            assert first not in json.dumps(audit)
            # Operators cannot.
            r = await c.post("/api/users", json={"username": "op", "dis": "Op", "role": "operator",
                                                 "password": "a long enough pw"})
            assert r.status_code == 200
        app = app_with("")
        async with app.router.lifespan_context(app), httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=("192.168.1.9", 5000)), base_url="http://bms") as op:
            await op.post("/api/auth/login", json={"username": "op", "password": "a long enough pw"})
            assert (await op.get("/api/mqtt-logins")).status_code == 403
            await op.post("/api/auth/logout")
            await op.post("/api/auth/login", json={"username": ADMIN["username"], "password": ADMIN["password"]})
            # Without the helper (a development machine, a Pi): not available.
            assert (await op.get("/api/mqtt-logins")).json() == {"available": False, "logins": []}
            assert (await op.post("/api/mqtt-logins", json={"name": "plug1"})).status_code == 404
    finally:
        await engine.dispose()
