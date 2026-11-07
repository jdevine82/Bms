# Milestone 12a: Site server packaging and Tailscale remote access

Status: **in progress** (7 Nov 2026). Main spec: 1 (support access), 7 (accounts), 8 (networking and security), 11 (deployment), 16 (bench test 12), 18 (milestone 12). Milestone 12b (remote updates with snapshot and rollback) builds on this.

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | How milestone 12 is split | **Two parts.** 12a: the site server packaging, Tailscale and the access policy. 12b: remote updates with snapshot, health check and automatic rollback (server and Pi). |
| 2 | How sites are kept apart on Tailscale | **One tailnet per customer.** A customer's people join their own tailnet and can reach only that customer's site servers. A customer with several sites uses one tag per site, so the policy decides who reaches which site. |
| 3 | How support reaches the sites | **Each site server is shared from the customer's tailnet into the support tailnet** (Tailscale node sharing). Support uses one tailnet for every site; the customer sees the share and can revoke it (main spec 1, support access). |
| 4 | The containers | **Two LXC containers on Proxmox, Tailscale on the host.** `bms-app` holds the backend, which serves the web app, plus Mosquitto. `bms-db` holds PostgreSQL 16 with TimescaleDB. `bms-mstp` stays on the host (milestone 4c). This changes main spec 11 (five containers); recorded in section 19. |
| 5 | Defaults (proposed) | See "Proposed defaults" below. |

## The layout

```
 customer tailnet ─── Proxmox host (Tailscale, tag:bms-site-<site>) ── tailscale serve :443 ──┐
                       │  vmbrbms 10.77.0.1/24 (private, no port)                           │
                       │    ├─ bms-app 10.77.0.10  ◄─────────────────────────────────────────┘ HTTP :80
                       │    │   also on vmbr0 (site LAN, HTTP :80) and the device bridge (BACnet/IP, Modbus TCP)
                       │    └─ bms-db  10.77.0.20  PostgreSQL :5432, from 10.77.0.10 only
                       └─ bms-mstp@<trunk> on the host, API on 10.77.0.1:47900+
```

- **Private bridge `vmbrbms`** (no physical port) carries the app ↔ database traffic and the HTTPS proxy. The database is on no other network.
- **`bms-app`:**
  - LAN: the web app is plain HTTP on port 80 (main spec 8).
  - Device network: BACnet/IP broadcasts, Modbus TCP, MQTT devices.
  - USB RS485 adapters for Modbus RTU are passed through by their stable names (`/dev/ttyBMS-*`, `deploy/udev`).
- **HTTPS for remote users:** `tailscale serve` on the host (port 443, a Tailscale certificate renewed by Tailscale) passes requests to `http://10.77.0.10:80`. Only the host has `10.77.0.1`, so the backend trusts `X-Forwarded-For` and the Tailscale identity headers only from that address (`BMS_TRUSTED_PROXIES`).
- **Release:** `deploy/build-release.sh` makes `devine-bms-<version>.tar.gz`, containing the backend, the built web app, `config/` and the container scripts. The site is installed from it, and milestone 12b updates from it.

## Scripts (`deploy/`)

- `deploy/proxmox/create-site.sh --site <name> --release <tarball> [options]`, run as root on the Proxmox host. It:
  1. downloads the Debian 13 template;
  2. creates `vmbrbms` if missing;
  3. creates `bms-db` and `bms-app` (unprivileged, start at boot, `bms-db` first);
  4. passes the RS485 adapters through;
  5. pushes the release and runs the container scripts;
  6. writes the secrets (the database password, the Mosquitto password) to `/etc/devine-bms/bms.env` in `bms-app`, mode 0640, never anywhere else;
  7. prints the setup next steps.

  It is safe to run again (it keeps existing containers and secrets). `DRY_RUN=1` prints what it would do.
- `deploy/containers/provision-db.sh`: PostgreSQL 16 and TimescaleDB from their apt repositories. It listens on `10.77.0.20` only, with `scram-sha-256` from `10.77.0.10` only.
- `deploy/containers/provision-app.sh`: Python venv, the backend, Mosquitto (no anonymous access), the `bms-backend` service with migrations before start.
- `deploy/proxmox/tailscale-host.sh --site <name>`: installs Tailscale and runs `tailscale up` with `--advertise-tags=tag:bms-site-<name>` (interactive login by default; `TS_AUTHKEY` in the environment only for an unattended install), then `tailscale serve --bg --https=443 http://10.77.0.10:80`.
- `deploy/tailscale/policy.py sites.json`: writes a customer tailnet's policy file:
  - `tagOwners`;
  - a group per site;
  - grants: each group to its own site on 443; customer admins to all their sites; shared-in support (`autogroup:shared`) to 443 and SSH;
  - **policy `tests`** that prove each group is blocked from the other sites. Tailscale refuses a policy whose tests fail.

## Backend

- `BMS_TRUSTED_PROXIES`: addresses (or networks) whose `X-Forwarded-For` names the client; loopback is always trusted. Otherwise the peer address is the client.
- **Tailscale identity:** from a trusted proxy, `Tailscale-User-Login` (set by `tailscale serve`) is recorded in the login audit entry: "logged in over Tailscale as name@example.com". The site account still decides what the person can do (main spec 7).
- **Support logins:** a login to a platform account (support) is written to the audit log as `support-login` with the Tailscale identity, so the customer's admin can see it (main spec 1).

## Proposed defaults (decision 5)

- **Containers:** Debian 13.
  - `bms-db`: 2 cores, 2 GB RAM, 32 GB disk.
  - `bms-app`: 2 cores, 2 GB RAM, 16 GB disk.
  - Container IDs 201 (db) and 202 (app), set with `--ctid-base`.
- **Bridges:** private bridge `vmbrbms` 10.77.0.0/24; LAN `vmbr0` (DHCP unless `--lan-ip`); device bridge `vmbr1` (`--device-bridge`, `none` for a shared LAN).
- **Tailscale:** interactive login (the URL is printed). Tags are named `tag:bms-site-<site>`.
