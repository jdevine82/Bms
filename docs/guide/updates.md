# Updates

For installers, support and the site's admins. Decision 031; spec `docs/specs/m12b-remote-updates.md`.

An update is a **release file** (`devine-bms-<version>.tar.gz`) uploaded in **Setup → Updates** by an admin. On a site
server, the updater on the Proxmox host does the work and can put the site back as it was. Each standby touch screen is
then updated by the server.

## Making a release (Devine BMS staff)

On a development machine, from a checkout with everything committed:

```bash
(cd web && npm ci && npm run build)
```

```bash
deploy/build-release.sh
```

The release is `dist/devine-bms-<version>.tar.gz`, where the version comes from `backend/pyproject.toml`; raise it for
each release. It holds only committed files (never a local `site.json` or `.env`) and the built web app. The script
refuses to run with uncommitted changes.

**Releases are not checked for who made them** (decision 031): the site checks only that the file is shaped like a
release. Only install releases from Devine BMS. Anyone with an admin account can install code on the site server, so
treat admin accounts with that much care.

## Updating a site server

1. **Setup → Updates** shows the version running.
2. Choose the release file and press **Install**. Confirm.
3. The page shows each step as it happens:
   1. **Before:** the version running and the devices online are noted.
   2. **Snapshots** of both containers (`bms-pre-<version>-<time>`).
   3. **Install:** the release goes into `bms-app` and installs side by side. The web app restarts; the page keeps
      checking by itself.
   4. **Health check (up to 3 minutes):**
      - the new version answers;
      - every device that was online is online again;
      - it stays up for a minute without restarting.
4. **Passed:** the new version runs, and the release is sent on to the standby touch screens.

   **Failed:** both containers are rolled back to the snapshot, the old version is checked, and the page says why. A
   rollback loses the history and audit entries written during those few minutes. The page says so, and the failure
   and its reason are recorded in the audit log.

The site is down for about a minute during an update. The update needs internet (it installs Python packages). The last
three snapshots are kept; older ones are deleted.

## Touch screens (Pis)

- **Standby HMIs** are updated by the site server after its own update passes. The Updates page lists each HMI with its
  version, its last update and the last release sent.
- **A standalone Pi** has its own Setup → Updates page.
- On a Pi:
  1. It copies the release onto its config partition, turns off the read-only root and restarts.
  2. It installs the release.
  3. It checks the new version answers within 2 minutes, and switches back to the old one if not.
  4. It turns the read-only root back on and restarts.

  This takes about 3 minutes, and a standby HMI cannot take over meanwhile. An HMI in direct mode refuses an update until
  control is handed back.
- Pis set up before updates existed need `provision.sh` run again once, to get the versioned layout.

## If something goes wrong

| What you see | What to do |
|---|---|
| "the updater on the host does not answer" | On the host, run `systemctl status bms-updater` and `journalctl -u bms-updater`. Check `BMS_UPDATER_TOKEN` is the same in `/etc/devine-bms/updater.env` and in `bms-app`'s `bms.env`. |
| "cannot snapshot the database container" | The containers' storage cannot take snapshots (for example `dir`). Nothing was changed. Move them to LVM-thin or ZFS. |
| "devices not back online" | A device did not answer after the update; the update was rolled back. Check the device, then try again. |
| "THE ROLLBACK FAILED" or "the site does not answer" | The site needs attention. On the host, `pct listsnapshot 202` lists the snapshots; `pct stop 202 && pct rollback 202 <snapshot> && pct start 202` (and the same for 201) restores them by hand. |
| The updater says "stopped during the update" | The host restarted mid-update. Check the site, and roll back by hand if needed. |
| A Pi did not update | Check `/var/lib/devine-bms/config/updates/last-update.json` on the Pi, and run `journalctl -u bms-pi-update`. |

The updater's own record is `/var/lib/devine-bms/updater/state.json` on the host.
