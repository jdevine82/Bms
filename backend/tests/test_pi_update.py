"""Installing a release side by side (deploy/containers/install-release.sh) and updating a Pi with a
read-only root (hmi/standalone/bms-pi-update), with the system commands replaced (milestone 12b).
On a real Pi: bench test 12. No database needed."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

from test_updater import release

REPO = Path(__file__).resolve().parents[2]
INSTALL = REPO / "deploy" / "containers" / "install-release.sh"
PI_UPDATE = REPO / "hmi" / "standalone" / "bms-pi-update"


def full_release(tmp_path: Path, version: str) -> Path:
    """A release whose install-release.sh is the real one."""
    d = tmp_path / f"r{version}"
    d.mkdir()
    return release(d, version, extra={"deploy/containers/install-release.sh": INSTALL.read_text()})


def _script(path: Path, text: str) -> str:
    path.write_text("#!/bin/bash\n" + text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def install(tmp_path: Path, tarball: Path) -> subprocess.CompletedProcess:
    fake_pip = _script(tmp_path / "pip.sh", 'mkdir -p "$1/venv/bin"\n')
    return subprocess.run(["bash", str(INSTALL), str(tarball), "--no-restart"], capture_output=True, text=True,
                          env={**os.environ, "BMS_BASE": str(tmp_path / "opt"), "BMS_PIP_INSTALL": fake_pip})


def test_install_side_by_side_and_keep_three(tmp_path):
    (tmp_path / "opt").mkdir()
    for i, v in enumerate(["0.1.0", "0.2.0", "0.3.0", "0.4.0"]):
        r = install(tmp_path, full_release(tmp_path, v))
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip().endswith(f"installed {v} (previous {['none', '0.1.0', '0.2.0', '0.3.0'][i]})")
    opt = tmp_path / "opt"
    assert os.readlink(opt / "current") == str(opt / "0.4.0")
    assert (opt / "current" / "web" / "dist" / "index.html").is_file()
    assert sorted(p.name for p in opt.iterdir() if p.is_dir() and not p.is_symlink()) == ["0.2.0", "0.3.0", "0.4.0"]
    # Installing the version in use again works and keeps the link valid.
    (tmp_path / "again").mkdir()
    r = install(tmp_path, full_release(tmp_path / "again", "0.4.0"))
    assert r.returncode == 0 and "installed 0.4.0 (previous 0.4.0)" in r.stdout


def test_install_refuses_a_broken_release(tmp_path):
    (tmp_path / "opt").mkdir()
    r = install(tmp_path, release(tmp_path, "0.2.0", drop="web/dist/index.html",
                                  extra={"deploy/containers/install-release.sh": INSTALL.read_text()}))
    assert r.returncode != 0 and "no built web app" in r.stderr
    assert not (tmp_path / "opt" / "current").exists()


class Pi:
    """A Pi: /opt/devine-bms with 0.1.0 installed, the config partition, and logged system commands."""

    def __init__(self, tmp_path: Path, overlay_active: bool = True, broken: str = ""):
        self.t = tmp_path
        self.base, self.conf, self.log = tmp_path / "opt", tmp_path / "conf", tmp_path / "commands.log"
        self.base.mkdir()
        self.conf.mkdir()
        assert install(tmp_path, full_release(tmp_path, "0.1.0")).returncode == 0
        logcmd = _script(tmp_path / "log.sh", f'echo "$*" >> {self.log}\n')
        health = tmp_path / "health.json"
        # "Restarting the backend" makes it answer with the version in current/, unless that version is broken.
        restart = _script(tmp_path / "restart.sh", f'''v=$(cat {self.base}/current/VERSION | tr -d '[:space:]')
if [ "$v" = "{broken}" ]; then echo '{{}}' > {health}; else echo "{{\\"version\\": \\"$v\\"}}" > {health}; fi
echo "restart $v" >> {self.log}
''')
        self.env = {**os.environ, "BMS_CONF": str(self.conf), "BMS_BASE": str(self.base), "BMS_RW_CMD": "",
                    "BMS_RO_CMD": "", "BMS_OVERLAY_OFF_CMD": f"{logcmd} overlay-off",
                    "BMS_OVERLAY_ON_CMD": f"{logcmd} overlay-on",
                    "BMS_OVERLAY_ACTIVE_CMD": "true" if overlay_active else "false",
                    "BMS_REBOOT_LATER_CMD": f"{logcmd} reboot-later", "BMS_REBOOT_CMD": f"{logcmd} reboot",
                    "BMS_RESTART_CMD": restart, "BMS_HEALTH_URL": f"file://{health}", "BMS_UPDATE_WAIT_S": "2",
                    "BMS_PIP_INSTALL": str(tmp_path / "pip.sh")}

    def run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(PI_UPDATE), *args], capture_output=True, text=True, env=self.env)

    def commands(self) -> list[str]:
        return self.log.read_text().split("\n")[:-1] if self.log.exists() else []

    def last(self) -> dict:
        return json.loads((self.conf / "updates" / "last-update.json").read_text())


def test_stage_keeps_the_release_root_only_and_reboots(tmp_path):
    pi = Pi(tmp_path)
    r = pi.run("stage", str(full_release(tmp_path, "0.2.0")))
    assert r.returncode == 0, r.stderr
    pending = pi.conf / "updates" / "pending.tar.gz"
    assert pending.is_file() and stat.S_IMODE(pending.stat().st_mode) == 0o600
    assert pi.commands() == ["overlay-off", "reboot-later"]


def test_stage_refuses_anything_but_a_release(tmp_path):
    pi = Pi(tmp_path)
    secret = tmp_path / "shadow"
    secret.write_text("root:secret")
    r = pi.run("stage", str(secret))
    assert r.returncode != 0 and "not a Devine BMS release" in r.stderr
    assert not (pi.conf / "updates" / "pending.tar.gz").exists() and pi.commands() == []


def test_apply_installs_checks_and_turns_the_overlay_back_on(tmp_path):
    pi = Pi(tmp_path, overlay_active=False)  # after the reboot the overlay is off
    assert pi.run("stage", str(full_release(tmp_path, "0.2.0"))).returncode == 0
    r = pi.run("apply")
    assert r.returncode == 0, r.stderr
    assert os.readlink(pi.base / "current") == str(pi.base / "0.2.0")
    assert pi.last()["state"] == "passed" and pi.last()["version"] == "0.2.0" and pi.last()["from"] == "0.1.0"
    assert not (pi.conf / "updates" / "pending.tar.gz").exists()
    assert pi.commands()[-3:] == ["restart 0.2.0", "overlay-on", "reboot"]
    assert pi.run("apply").returncode == 0 and pi.commands()[-1] == "reboot"  # nothing waiting: nothing done


def test_apply_switches_back_when_the_new_version_does_not_answer(tmp_path):
    pi = Pi(tmp_path, overlay_active=False, broken="0.2.0")
    pi.run("stage", str(full_release(tmp_path, "0.2.0")))
    r = pi.run("apply")
    assert r.returncode == 0, r.stderr
    assert os.readlink(pi.base / "current") == str(pi.base / "0.1.0")
    assert pi.last()["state"] == "rolled-back" and "0.1.0 is back" in pi.last()["reason"]
    assert pi.commands()[-4:] == ["restart 0.2.0", "restart 0.1.0", "overlay-on", "reboot"]


def test_apply_does_nothing_if_the_overlay_is_still_on(tmp_path):
    pi = Pi(tmp_path, overlay_active=True)
    pi.run("stage", str(full_release(tmp_path, "0.2.0")))
    r = pi.run("apply")
    assert r.returncode == 0
    assert os.readlink(pi.base / "current") == str(pi.base / "0.1.0")
    assert pi.last()["state"] == "failed" and "overlay" in pi.last()["reason"]
