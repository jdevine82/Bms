"""The site server's update agent (milestone 12b, decision 031), with a stand-in for pct and the
site's /api/health. It runs on the Proxmox host for real (bench test 12). No database needed."""

from __future__ import annotations

import importlib.util
import io
import json
import tarfile
import threading
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOKEN = "update-token-0123456789abcdef"


def _mod():
    spec = importlib.util.spec_from_file_location("bms_updater", REPO / "deploy" / "updater" / "bms_updater.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


U = _mod()


def release(tmp_path: Path, version: str = "0.2.0", drop: str | None = None, extra: dict | None = None,
            top: str | None = None) -> Path:
    top = top or f"devine-bms-{version}"
    files = {"VERSION": version + "\n", "backend/pyproject.toml": "[project]\n", "web/dist/index.html": "<html>",
             "deploy/containers/install-release.sh": "#!/bin/bash\n", **(extra or {})}
    path = tmp_path / f"{top}.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        dir_info = tarfile.TarInfo(top)
        dir_info.type = tarfile.DIRTYPE
        tar.addfile(dir_info)
        for name, text in files.items():
            if name == drop:
                continue
            data = text.encode()
            info = tarfile.TarInfo(name if name.startswith("/") or name.startswith("..") else f"{top}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


class FakeSite:
    """pct and /api/health. `after` is what the site answers once installed; rollback restores `before`."""

    def __init__(self, before="0.1.0", after="0.2.0", devices_after=("meter", "ahu"), install_rc=0,
                 snapshot_rc=0, restarts=("0", "0")):
        self.version = before
        self.before = before
        self.after = after
        self.devices = {"meter", "ahu"}
        self.devices_after = set(devices_after)
        self.install_rc = install_rc
        self.snapshot_rc = snapshot_rc
        self.restarts = list(restarts)
        self.cmds: list[list[str]] = []
        self.reports: list[dict] = []
        self.snaps = {"201": ["bms-pre-0_0_7-20260101000000", "bms-pre-0_0_8-20260201000000",
                              "bms-pre-0_0_9-20260301000000"], "202": []}
        self.up = True

    def run(self, cmd, stdin=None, timeout=0):
        self.cmds.append(cmd)
        if cmd[:2] == ["pct", "snapshot"]:
            if self.snapshot_rc:
                return self.snapshot_rc, "snapshot feature is not available"
            self.snaps[cmd[2]].append(cmd[3])
            return 0, ""
        if cmd[:2] == ["pct", "exec"] and "bash" in cmd:
            if self.install_rc:
                return self.install_rc, "ERROR: [install] pip failed"
            self.version, self.devices = self.after, set(self.devices_after)
            return 0, f"installed {self.after} (previous {self.before})"
        if "NRestarts" in cmd:
            return 0, self.restarts.pop(0) if self.restarts else "0"
        if cmd[:2] == ["pct", "stop"]:
            self.up = False
        if cmd[:2] == ["pct", "rollback"]:
            self.version, self.devices = self.before, {"meter", "ahu"}
        if cmd[:2] == ["pct", "start"]:
            self.up = True
        if cmd[:2] == ["pct", "listsnapshot"]:
            return 0, "\n".join(f"`-> {s}  2026-01-01 description" for s in self.snaps[cmd[2]])
        return 0, ""

    def http(self, method, url, body=None, token=None, timeout=0):
        if url.endswith("/api/health"):
            if not self.up or self.version is None:
                return None
            return {"status": "ok", "version": self.version,
                    "devices": [{"name": d, "online": True} for d in sorted(self.devices)]}
        if url.endswith("/api/updates/report"):
            assert token == TOKEN
            self.reports.append(body)
            return {"ok": True}
        return None


def updater(tmp_path, site: FakeSite):
    clock = {"t": 0.0}

    def sleep(s):
        clock["t"] += s

    return U.Updater(tmp_path / "state", token=TOKEN, runner=site.run, http=site.http, sleep=sleep,
                     clock=lambda: clock["t"], answer_s=30, stable_s=10, poll_s=5)


def finish(u):
    u.thread.join(timeout=10)
    assert not u.thread.is_alive()
    return u.status()


def test_a_good_update_passes_and_keeps_three_snapshots(tmp_path):
    site = FakeSite()
    u = updater(tmp_path, site)
    assert u.start(release(tmp_path)) == {"ok": True, "version": "0.2.0"}
    st = finish(u)
    assert st["state"] == "passed" and st["from"] == "0.1.0" and st["version"] == "0.2.0"
    snaps = [c for c in site.cmds if c[1] == "snapshot"]
    assert [c[2] for c in snaps] == ["201", "202"] and snaps[0][3].startswith("bms-pre-0_2_0-")
    push = next(c for c in site.cmds if c[1] == "push")
    assert push[2] == "202" and push[4] == "/opt/devine-bms/releases/devine-bms-0.2.0.tar.gz"
    deleted = [c for c in site.cmds if c[1] == "delsnapshot"]
    assert [c[2:] for c in deleted] == [["201", "bms-pre-0_0_7-20260101000000"]]  # 4 of ours on 201: oldest goes
    assert not any(c[1] == "rollback" for c in site.cmds)
    assert site.reports[-1]["state"] == "passed" and st["reported"] is True
    assert not list((tmp_path / "state").glob("release-*"))


def test_a_failed_install_rolls_both_containers_back(tmp_path):
    site = FakeSite(install_rc=1)
    u = updater(tmp_path, site)
    u.start(release(tmp_path))
    st = finish(u)
    assert st["state"] == "rolled-back" and "pip failed" in st["reason"]
    order = [c[1:3] for c in site.cmds if c[1] in ("stop", "rollback", "start")]
    assert order == [["stop", "202"], ["stop", "201"], ["rollback", "201"], ["rollback", "202"],
                     ["start", "201"], ["start", "202"]]
    assert any("were lost with the rollback" in s["text"] for s in st["steps"])
    assert site.reports[-1]["state"] == "rolled-back"


def test_a_version_that_does_not_come_up_is_rolled_back(tmp_path):
    site = FakeSite(after=None)
    u = updater(tmp_path, site)
    u.start(release(tmp_path))
    st = finish(u)
    assert st["state"] == "rolled-back" and "did not answer within 30 s" in st["reason"]
    assert site.version == "0.1.0"


def test_devices_that_do_not_come_back_fail_the_update(tmp_path):
    site = FakeSite(devices_after=("meter",))
    u = updater(tmp_path, site)
    u.start(release(tmp_path))
    st = finish(u)
    assert st["state"] == "rolled-back" and "devices not back online" in st["reason"] and "ahu" in st["reason"]


def test_a_restart_during_the_stability_check_fails_the_update(tmp_path):
    site = FakeSite(restarts=("0", "1"))
    u = updater(tmp_path, site)
    u.start(release(tmp_path))
    st = finish(u)
    assert st["state"] == "rolled-back" and "restarted" in st["reason"]


def test_storage_without_snapshots_changes_nothing(tmp_path):
    site = FakeSite(snapshot_rc=1)
    u = updater(tmp_path, site)
    u.start(release(tmp_path))
    st = finish(u)
    assert st["state"] == "failed" and "nothing was changed" in st["reason"]
    assert not any(c[1] in ("push", "exec", "rollback", "stop") for c in site.cmds)
    assert site.reports[-1]["state"] == "failed"


def test_a_site_that_does_not_answer_is_not_updated(tmp_path):
    site = FakeSite()
    site.up = False
    u = updater(tmp_path, site)
    u.start(release(tmp_path))
    st = finish(u)
    assert st["state"] == "failed" and "nothing was changed" in st["reason"]
    assert not any(c[1] == "snapshot" for c in site.cmds)


@pytest.mark.parametrize("kwargs, message", [
    ({"drop": "VERSION"}, "no VERSION"),
    ({"drop": "web/dist/index.html"}, "no web/dist/index.html"),
    ({"drop": "deploy/containers/install-release.sh"}, "install-release.sh"),
    ({"top": "something-else"}, "not a Devine BMS release"),
    ({"extra": {"../../etc/passwd": "x"}}, "unsafe path"),
    ({"extra": {"VERSION": "0.2.0; rm -rf /"}}, "VERSION is not valid"),
])
def test_bad_releases_are_refused(tmp_path, kwargs, message):
    with pytest.raises(U.ReleaseError, match=message):
        U.check_release(release(tmp_path, **kwargs))


def test_not_a_tarball(tmp_path):
    f = tmp_path / "x.tar.gz"
    f.write_bytes(b"hello")
    with pytest.raises(U.ReleaseError, match="not a .tar.gz"):
        U.check_release(f)


def test_one_update_at_a_time_and_state_survives_a_restart(tmp_path):
    site = FakeSite()
    u = updater(tmp_path, site)
    gate = threading.Event()
    original = site.run

    def slow(cmd, stdin=None, timeout=0):
        if cmd[1] == "snapshot":
            gate.wait(5)
        return original(cmd, stdin, timeout)

    site.run = slow
    u.run = slow
    u.start(release(tmp_path))
    with pytest.raises(RuntimeError, match="already running"):
        u.start(release(tmp_path, "0.3.0"))
    # The agent stops mid-update: the next start says so.
    again = U.Updater(tmp_path / "state", token=TOKEN, runner=site.run, http=site.http)
    assert again.status()["state"] == "failed" and "stopped during the update" in again.status()["reason"]
    gate.set()
    finish(u)


def test_the_http_side_needs_the_token(tmp_path):
    site = FakeSite()
    u = updater(tmp_path, site)
    server = U.make_server(u, ("127.0.0.1", 0), TOKEN)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        def call(method, path, data=None, token=TOKEN):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
            if token:
                req.add_header(U.TOKEN_HEADER, token)
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())

        import urllib.error

        assert call("GET", "/status", token=None)[0] == 403
        assert call("GET", "/status", token="wrong")[0] == 403
        assert call("GET", "/status")[1]["state"] == "idle"
        assert call("POST", "/update", b"not a tarball")[0] == 422
        code, body = call("POST", "/update", release(tmp_path).read_bytes())
        assert code == 202 and body["version"] == "0.2.0"
        finish(u)
        assert call("GET", "/status")[1]["state"] == "passed"
    finally:
        server.shutdown()
