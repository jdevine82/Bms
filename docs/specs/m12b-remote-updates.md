# Milestone 12b: Updates with snapshot, health check and rollback

Status: **built**; bench test 12 on real Proxmox and a real Pi to do (8 Nov 2026). Main spec: 11 (updates), 7 (admins do updates), 16 (bench test 12), 18 (milestone 12). Builds on 12a (`deploy/`, decision 030).

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | How an update is started | **Upload in the web app:** Setup → Updates (admins). It shows the version, the progress, and the history of updates and rollbacks. |
| 2 | How the app reaches Proxmox | **A small updater agent on the host** (`bms-updater`). It listens on the private bridge only (`10.77.0.1:8701`), with a shared token (`BMS_UPDATER_TOKEN`). The agent alone snapshots, installs, health-checks and rolls back; the container has no Proxmox rights. |
| 3 | Checking a release | **No check of who made it** (decided). The agent checks only that the file is a well-formed release: one top folder, `VERSION`, the backend, the built web app, no absolute paths or `..`, at most 200 MB. **Risk:** any site admin can install modified code, so admin accounts are as powerful as root on the host. Recorded so it can be revisited (signed releases need no other change). |
| 4 | Pi HMIs | **The server pushes to its standby HMIs** after its own update passes its health check (`BMS_HMI_URLS`, the standby token). A standalone Pi has its own Setup → Updates page. Kiosk-only Pis need nothing. |
| 5 | Details (proposed) | See "Proposed details". |

## The site server (agent on the host)

1. The page uploads the release to the backend, which streams it to the agent with the token.
   - The agent refuses it if an update is running, if the file is not a well-formed release, or if the containers' storage cannot snapshot (checked first, before anything changes).
2. **Before:** the agent notes the version and which devices are online (`/api/health`), then takes a Proxmox snapshot of both containers, `bms-pre-<version>-<time>`.
3. **Install:** the release goes into `bms-app` and its own `deploy/containers/install-release.sh` runs:
   - it installs side by side in `/opt/devine-bms/<version>` (venv, backend);
   - it switches `/opt/devine-bms/current` and restarts `bms-backend` (migrations run at start).
4. **Health check (3 minutes at most):**
   - `/api/health` answers with the new version;
   - every device that was online before is online again;
   - the service then stays up for 60 seconds without restarting.
5. **Passed:** the agent tells the backend (`POST /api/updates/report`, token). The backend writes the audit entry and pushes the release to its standby HMIs. Snapshots older than the last three of ours are deleted.
6. **Failed:** the agent stops both containers, rolls both back to the snapshot (the database too, because migrations may have run), starts them, waits for the old version to answer, then reports the failure and its reason to the backend. The backend writes it to the audit log.
   - The few minutes of history and audit entries between the snapshot and the rollback are lost; the report says so.
7. The agent keeps its state in `/var/lib/devine-bms/updater/state.json`, so the page shows the result after the backend restarts or is rolled back.

## Pi HMIs (standby and standalone)

- The release is saved on the config partition (the root is read-only) and `bms-pi-update stage` runs (through sudo). It refuses while the HMI is in direct mode.
- It turns the overlay off and reboots. At boot `bms-pi-update.service` (before the backend):
  1. installs into `/opt/devine-bms/<version>` and switches `/opt/devine-bms/current`;
  2. starts the backend and checks `/api/health` for the new version (2 minutes);
  3. **on failure** switches `current` back and restarts the backend;
  4. writes the result to the config partition, turns the overlay on and reboots.

  The whole update takes about 3 minutes, and the HMI cannot take over meanwhile.
- The server shows each HMI's version and the last result on the Updates page (from the HMI's standby state).
- The Pi's layout changes to versioned folders with a `current` link, as on the server.

## Proposed details (decision 5)

- **Agent:** Python 3 standard library only, `deploy/updater/bms_updater.py`; systemd unit `bms-updater`. `create-site.sh` installs it and makes the token (in `/etc/devine-bms/updater.env` on the host and `bms.env` in `bms-app`).
- **Health limits:** 180 s to answer, 60 s stable. Devices: every device online before must be online after; a device that went down by coincidence fails the update (safe side).
- **Releases kept:** the last 3 snapshots and the last 3 installed versions in each container and on each Pi.
- **Internet:** installing a release runs `pip install`, so the site needs internet (PyPI) during an update, as at install.

## What was built

- **The host agent:**
  - `deploy/updater/bms_updater.py` with its unit `bms-updater.service`, using the standard library only.
  - It checks the release, snapshots `bms-db` then `bms-app`, pushes the release and runs the release's own installer.
  - It health-checks: the version, the devices, then 60 s stable with no new restarts.
  - On failure it stops, rolls back and restarts both containers, then waits for the old version.
  - It keeps the last 3 snapshots, reports to the backend, and keeps its state in `state.json`. One update runs at a time.
  - If the agent restarts mid-update, its state shows as failed, so it can be checked by hand.
  - `create-site.sh` installs it and makes its token, which reaches `bms-app` on stdin.
- **Container side:** `deploy/containers/install-release.sh`. It installs side by side, switches `current` atomically and keeps 3 versions; `provision-app.sh` uses it too.
- **Backend:**
  - `updates/` with `release.py` (the same checks as the agent; a test keeps them in step) and `service.py`.
  - `api/updates_api.py`: `GET /api/updates`, `POST /api/updates/upload` (admins), `POST /api/updates/report` (agent token), `POST /api/standby/update` (standby token, standby HMIs only).
  - The HMIs' standby state now has `version` and `lastUpdate`.
- **Pi:**
  - `hmi/standalone/bms-pi-update` (`stage` and `apply`) and `bms-pi-update.service`.
  - The versioned layout (`/opt/devine-bms/<version>`, `current`).
  - `BMS_PI_UPDATE_CMD` and `BMS_UPDATE_DIR` in the env file.
  - A sudoers entry for `bms-pi-update stage` only. `stage` accepts only a release-shaped file and keeps its copy root-only.
- **Web:** Setup → Updates, with the version, Install, the progress steps, the result, the standby HMIs (version, last update, last push) and the history. The audit filter has `update-start`, `update` and `update-hmi`.
- **Tests:**
  - `tests/test_updater.py` (16): the agent with a stand-in pct.
  - `tests/test_updates.py` (5): a real backend and agent on two ports, the HMI push, a rollback report, permissions, Pi staging, the two release checks agreeing.
  - `tests/test_pi_update.py` (7): the installer and the Pi update with stand-in commands, including the switch back.
  - `tests/test_deploy.py`: the agent install in the dry run.
  - `web/tests/updates.mjs`: in the browser, with `tests/fake_updater.py`.

Not tested here: `pct snapshot`, `rollback` and `listsnapshot` output on real Proxmox storage; `raspi-config` overlay switching and the reboots on a real Pi; `pip install` during an update (needs internet). These are bench test 12.

## How to set up

- **Site server:** nothing extra. `create-site.sh` installs the agent; run it again on a site made before 12b. The containers' storage must support snapshots (LVM-thin or ZFS). With `dir` storage the update is refused before anything changes.
- **Updating:**
  1. Build a release from a committed checkout (`deploy/build-release.sh`).
  2. Open Setup → Updates, choose the file, then Install.
  3. Watch the steps. The page carries on by itself while the site restarts.
- **Pis:** run `provision.sh` again on each Pi (versioned layout, `bms-pi-update`, sudoers). Standby HMIs then update by themselves after the server; a standalone Pi has Setup → Updates.
- **Checking:**
  - the agent: `systemctl status bms-updater` and `/var/lib/devine-bms/updater/state.json` on the host;
  - a Pi: `/var/lib/devine-bms/config/updates/last-update.json` and `journalctl -u bms-pi-update`.
