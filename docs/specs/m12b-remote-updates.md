# Milestone 12b: Updates with snapshot, health check and rollback

Status: **in progress** (8 Nov 2026). Main spec: 11 (updates), 7 (admins do updates), 16 (bench test 12), 18 (milestone 12). Builds on 12a (`deploy/`, decision 030).

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
