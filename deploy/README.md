# Site server deployment (Proxmox VE)

Milestone 12a, decision 030, spec `../docs/specs/m12a-site-server-remote.md`.

A site server is a Proxmox VE host with two LXC containers. Tailscale runs on the host itself.

| Where | What | Address on the private bridge `vmbrbms` |
|---|---|---|
| Proxmox host | Tailscale with `tailscale serve` (HTTPS for remote users), `bms-mstp@<trunk>` (MS/TP, milestone 4c) | 10.77.0.1 |
| `bms-app` (CT 202) | The backend, which serves the web app (HTTP on port 80), and Mosquitto. Also on the site LAN and the device network. | 10.77.0.10 |
| `bms-db` (CT 201) | PostgreSQL 16 with TimescaleDB. On the private bridge only. | 10.77.0.20 |

## What you need

- Proxmox VE 8 or 9 on the site server (64-bit), with these bridges:
  - `vmbr0`: the site LAN;
  - `vmbr1`: the device network, if it is separate (main spec 8).
- The USB RS485 adapters, with the udev rules from `udev/` installed on the host, so they have stable names (`/dev/ttyBMS-*`).
- On your computer: a release made from a committed checkout.

```bash
(cd web && npm ci && npm run build)
```

```bash
deploy/build-release.sh
```

This writes `dist/devine-bms-<version>.tar.gz`. It contains only files under version control and refuses to build from uncommitted changes.

## Install a site

1. Copy the release and the `deploy/` folder to the host. On the host, as root:

   ```bash
   deploy/proxmox/create-site.sh --site plant1 --release devine-bms-0.1.0.tar.gz \
       --lan-ip 192.168.1.20/24,gw=192.168.1.1 --device-ip 192.168.20.10/24 --serial /dev/ttyBMS-rs485-1
   ```

   - With no separate device network, use `--device-bridge none`.
   - `DRY_RUN=1` first shows every command without changing anything.
   - The script is safe to run again: it keeps the containers and the secrets.
2. **First admin.** The first-run page is only offered from the server itself. Tunnel through the host and open http://localhost:8080 on your computer:

   ```bash
   ssh -L 8080:10.77.0.10:80 root@<host>
   ```

3. **Remote access:**
   - In the customer's tailnet, make the policy and save it under Access controls. It replaces the tailnet's policy, so the tailnet must be the customer's BMS tailnet:

     ```bash
     python3 deploy/tailscale/policy.py sites.json
     ```

   - Then, on the host:

     ```bash
     deploy/proxmox/tailscale-host.sh --site plant1
     ```

   - Open the login URL it prints, as a tag owner.
   - Finally, in the customer's admin console, **share** the machine `bms-plant1` with the support tailnet.
4. **MS/TP trunks:** install `bms-mstp` on the host (`../mstp/README.md`) with `BMS_MSTP_LISTEN=10.77.0.1:47900` (one port per trunk). In the BMS, set the trunk's service to `10.77.0.1:47900`.

## Where things are

- Settings and secrets: `/etc/devine-bms/bms.env` in `bms-app` (0640 root:bms). It holds the database password and the Mosquitto password (`BMS_MQTT_PASSWORD`; name it as `passwordEnv` for user `bms`). Add alarm delivery keys there by hand; running the script again keeps them.
- The release in use: `/opt/devine-bms/current`, a link to `/opt/devine-bms/<version>`. Milestone 12b updates by switching this link.
- Logs: run `journalctl -u bms-backend` in `bms-app`, and `journalctl -u postgresql@16-main` in `bms-db`.
- Check from the host:

  ```bash
  pct exec 202 -- curl -s http://127.0.0.1/api/health
  ```

  Then check from a phone and a laptop in the customer's tailnet: `https://bms-plant1.<tailnet>.ts.net/`.

## Updates (milestone 12b)

- An admin uploads a release in **Setup → Updates**. The page streams the file to `bms-updater` on the host (`10.77.0.1:8701`, token in `/etc/devine-bms/updater.env` and `bms.env`).
- The agent:
  1. checks the file's shape (not who made it, decision 031);
  2. snapshots both containers;
  3. installs with the release's own `deploy/containers/install-release.sh`;
  4. checks the site: the new version answers, the same devices are online, and it stays up 60 s;
  5. if the check fails, rolls both containers back.
- The page shows the steps and the result, and the history is in the audit log. A passed release then goes to the standby HMIs (`BMS_HMI_URLS`).
- Storage must support snapshots (LVM-thin or ZFS). The last 3 `bms-pre-*` snapshots are kept.
- An update needs internet (PyPI) for `pip install`.
- On the host:
  - `systemctl status bms-updater`;
  - `journalctl -u bms-updater`;
  - `/var/lib/devine-bms/updater/state.json`;
  - snapshots: `pct listsnapshot 202`.

## Bench test 12

- The site opens over Tailscale from a phone and a laptop.
- A person in another site's group is refused. The policy's own tests check this too, and Tailscale will not save a policy whose tests fail.
- Logins over Tailscale show "logged in over Tailscale as …" in the audit log, and support logins show as `support-login`.
