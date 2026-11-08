"""Site server packaging and Tailscale (milestone 12a): the customer tailnet policy, and the
Proxmox and Tailscale scripts in dry-run mode (they need Proxmox and a tailnet to run for real:
bench test 12). No database needed."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "deploy"


def _policy():
    spec = importlib.util.spec_from_file_location("bms_policy", DEPLOY / "tailscale" / "policy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SITES = {"customer": "Example", "owners": ["it@example.com"], "admins": ["boss@example.com"],
         "sites": [{"name": "plant1", "people": ["pat@example.com"]},
                   {"name": "office", "people": ["sam@example.com", "pat@example.com"]},
                   {"name": "store", "people": ["lee@example.com"]}]}


def test_each_group_reaches_only_its_own_site():
    p = _policy().build(SITES)
    assert p["tagOwners"]["tag:bms-site-plant1"] == ["it@example.com"]
    acl = {tuple(a["src"]): a["dst"] for a in p["acls"]}
    assert acl[("group:site-plant1",)] == ["tag:bms-site-plant1:443"]
    assert acl[("group:site-store",)] == ["tag:bms-site-store:443"]
    assert acl[("group:bms-admins",)] == [f"tag:bms-site-{s}:443" for s in ("plant1", "office", "store")]
    # Support comes in through node sharing, on HTTPS and SSH.
    assert acl[("autogroup:shared",)] == [f"tag:bms-site-{s}:443,22" for s in ("plant1", "office", "store")]
    assert all(a["action"] == "accept" for a in p["acls"])


def test_the_policy_tests_prove_the_blocking():
    tests = {t["src"]: t for t in _policy().build(SITES)["tests"]}
    lee = tests["lee@example.com"]
    assert lee["accept"] == ["tag:bms-site-store:443"]
    assert set(lee["deny"]) >= {"tag:bms-site-plant1:443", "tag:bms-site-office:443", "tag:bms-site-store:22"}
    # Pat is in two sites: never denied either of them.
    pat_denies = {d for t in _policy().build(SITES)["tests"] if t["src"] == "pat@example.com" for d in t["deny"]}
    assert "tag:bms-site-plant1:443" not in pat_denies and "tag:bms-site-office:443" not in pat_denies
    assert "tag:bms-site-store:443" in pat_denies
    assert tests["boss@example.com"]["accept"] == [f"tag:bms-site-{s}:443" for s in ("plant1", "office", "store")]


@pytest.mark.parametrize("bad, message", [
    ({**SITES, "sites": []}, "at least one site"),
    ({**SITES, "owners": []}, "owners"),
    ({**SITES, "sites": [{"name": "Plant 1", "people": []}]}, "site name"),
    ({**SITES, "sites": [{"name": "a", "people": ["x"]}]}, "not an email"),
    ({**SITES, "sites": [{"name": "a"}, {"name": "a"}]}, "twice"),
])
def test_bad_site_lists_are_refused(bad, message):
    mod = _policy()
    with pytest.raises(mod.PolicyError, match=message):
        mod.build(bad)


def test_the_script_writes_hujson(tmp_path):
    f = tmp_path / "sites.json"
    f.write_text(json.dumps(SITES))
    out = subprocess.run(["python3", str(DEPLOY / "tailscale" / "policy.py"), str(f)], capture_output=True,
                         text=True, check=True).stdout
    assert out.startswith("// Devine BMS access policy for Example")
    body = json.loads("\n".join(line for line in out.splitlines() if not line.startswith("//")))
    assert set(body) == {"tagOwners", "groups", "acls", "tests"}
    example = subprocess.run(["python3", str(DEPLOY / "tailscale" / "policy.py"),
                              str(DEPLOY / "tailscale" / "sites.example.json")], capture_output=True, text=True)
    assert example.returncode == 0, example.stderr


# ---------- the Proxmox and Tailscale scripts, dry run ----------

def _dry(script: str, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(DEPLOY / script), *args], capture_output=True, text=True,
                          env={**os.environ, "DRY_RUN": "1", "PATH": os.environ["PATH"], **(env or {})})


def test_create_site_dry_run(tmp_path):
    release = tmp_path / "devine-bms-0.1.0.tar.gz"
    release.write_bytes(b"")
    r = _dry("proxmox/create-site.sh", "--site", "plant1", "--release", str(release), "--lan-ip",
             "192.168.1.20/24,gw=192.168.1.1", "--device-ip", "192.168.20.10/24", "--serial",
             "/dev/ttyBMS-rs485-1")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "pct create 201" in out and "--hostname bms-db-plant1" in out
    assert "bridge=vmbrbms,ip=10.77.0.20/24" in out  # the database: the private bridge only
    db_line = next(line for line in out.splitlines() if "pct create 201" in line)
    assert "vmbr0" not in db_line and "vmbr1" not in db_line
    app_line = next(line for line in out.splitlines() if "pct create 202" in line)
    assert "bridge=vmbrbms,ip=10.77.0.10/24" in app_line
    assert "name=eth1,bridge=vmbr0,ip=192.168.1.20/24,gw=192.168.1.1" in app_line
    assert "name=eth2,bridge=vmbr1,ip=192.168.20.10/24" in app_line
    assert "--unprivileged 1" in app_line and "--onboot 1" in app_line
    assert "pct set 202 --dev0 path=/dev/ttyBMS-rs485-1,gid=20,mode=0660" in out
    assert "password on stdin" in out
    assert "BMS_MSTP_LISTEN=10.77.0.1:47900" in out
    # Milestone 12b: the updater agent on the host, its token never on a command line.
    assert "install -m 0755" in out and "/usr/local/lib/devine-bms/bms_updater.py" in out
    assert "systemctl enable bms-updater.service" in out and "updater.env (0600" in out
    assert "password and updater token on stdin" in out


@pytest.mark.parametrize("args, message", [
    (["--site", "Plant 1"], "--site"),
    (["--site", "p1", "--release", "/nope.tar.gz"], "--release"),
    (["--site", "p1", "--release", "REL", "--lan-ip", "fixed"], "--lan-ip"),
    (["--site", "p1", "--release", "REL"], "--device-ip"),
    (["--site", "p1", "--release", "REL", "--device-bridge", "none", "--serial", "/dev/ttyUSB0"], "stable names"),
])
def test_create_site_checks_its_arguments(tmp_path, args, message):
    release = tmp_path / "r.tar.gz"
    release.write_bytes(b"")
    r = _dry("proxmox/create-site.sh", *[str(release) if a == "REL" else a for a in args])
    assert r.returncode != 0 and message in r.stderr


def test_create_site_without_a_device_network(tmp_path):
    release = tmp_path / "r.tar.gz"
    release.write_bytes(b"")
    r = _dry("proxmox/create-site.sh", "--site", "p1", "--release", str(release), "--device-bridge", "none")
    assert r.returncode == 0, r.stderr
    assert "--net2" not in r.stdout and "name=eth1,bridge=vmbr0,ip=dhcp" in r.stdout


def test_tailscale_host_dry_run():
    r = _dry("proxmox/tailscale-host.sh", "--site", "plant1")
    assert r.returncode == 0, r.stderr
    assert "tailscale up --hostname bms-plant1 --advertise-tags tag:bms-site-plant1" in r.stdout
    assert "tailscale serve --bg --https=443 http://10.77.0.10:80" in r.stdout
    keyed = _dry("proxmox/tailscale-host.sh", "--site", "plant1", env={"TS_AUTHKEY": "tskey-auth-secret"})
    assert "tskey-auth-secret" not in keyed.stdout + keyed.stderr  # the key is never on a command line
    assert "--auth-key file:" in keyed.stdout


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck is not installed")
def test_the_scripts_pass_shellcheck():
    scripts = sorted(str(p) for p in DEPLOY.rglob("*.sh"))
    r = subprocess.run(["shellcheck", *scripts], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


@pytest.mark.skipif(not (REPO / "web" / "dist" / "index.html").is_file(), reason="the web app is not built")
def test_the_release_has_only_committed_files_and_no_secrets(tmp_path):
    r = subprocess.run(["bash", str(DEPLOY / "build-release.sh"), str(tmp_path)], capture_output=True, text=True,
                       env={**os.environ, "ALLOW_DIRTY": "1"})
    assert r.returncode == 0, r.stderr
    with tarfile.open(r.stdout.strip()) as tar:
        names = tar.getnames()
    top = {n.split("/")[1] for n in names if "/" in n}
    assert top == {"backend", "config", "deploy", "web", "COMMIT", "VERSION"}
    assert not any(n.endswith("/config/site.json") or n.endswith("/.env") or "/backend/tests/" in n for n in names)
    assert any(n.endswith("/web/dist/index.html") for n in names)
    assert any(n.endswith("/deploy/containers/provision-app.sh") for n in names)
