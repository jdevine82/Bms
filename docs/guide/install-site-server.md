# Installing a site server

For installers. A site server is a Proxmox VE host with two LXC containers and a private bridge. The scripts are in
`deploy/`; [`deploy/README.md`](../../deploy/README.md) has the same steps in short.

| Where | What | Address on the private bridge `vmbrbms` |
|---|---|---|
| Proxmox host | Tailscale with `tailscale serve` (HTTPS for remote users), the updater agent `bms-updater`, and `bms-mstp` for each MS/TP trunk | 10.77.0.1 |
| `bms-app` (CT 202) | The backend, which also serves the web app on port 80, and Mosquitto. Also on the site LAN and the device network. | 10.77.0.10 |
| `bms-db` (CT 201) | PostgreSQL 16 with TimescaleDB. On the private bridge only. | 10.77.0.20 |

## Before you start

- **Proxmox VE 8 or 9** on the server, 64-bit, with:
  - a bridge for the site LAN (`vmbr0`);
  - a bridge for the device network if it is separate (`vmbr1`);
  - **storage that can take snapshots** (LVM-thin or ZFS). Updates snapshot both containers and refuse to run without it.
- **Internet** during the install and during updates (apt and PyPI).
- **USB RS485 adapters**, with the udev rules installed on the host (`deploy/udev/99-devine-bms-rs485.rules`) so each has
  a stable name such as `/dev/ttyBMS-rs485-1`. Edit the rules file with each adapter's serial number
  (`udevadm info /dev/ttyUSB0 | grep SERIAL_SHORT`), copy it to `/etc/udev/rules.d/`, then
  `udevadm control --reload && udevadm trigger`.
- **The network plan:**
  - the server's LAN address;
  - its device-network address;
  - for each MS/TP trunk, its network number (101, 102, …) and, if it routes, its own labelled address.
- **A release** built from a committed checkout on your computer:

  ```bash
  (cd web && npm ci && npm run build)
  ```

  ```bash
  deploy/build-release.sh
  ```

  This writes `dist/devine-bms-<version>.tar.gz`.

## 1. Create the containers

Copy the release and the `deploy/` folder to the host. As root on the host, see what would happen first:

```bash
DRY_RUN=1 deploy/proxmox/create-site.sh --site plant1 --release devine-bms-0.1.0.tar.gz \
    --lan-ip 192.168.1.20/24,gw=192.168.1.1 --device-ip 192.168.20.10/24 --serial /dev/ttyBMS-rs485-1
```

Then run the same command without `DRY_RUN=1`.

| Option | Meaning |
|---|---|
| `--site` | The site name: lower-case letters, digits and `-`. It becomes the BMS site name and the container names. |
| `--release` | The release file. |
| `--lan-ip` | `dhcp` (default) or `ADDRESS/PREFIX,gw=GATEWAY` on the LAN bridge. |
| `--lan-bridge`, `--device-bridge` | The bridges (defaults `vmbr0`, `vmbr1`). `--device-bridge none` when devices are on the LAN. |
| `--device-ip` | The app's address on the device network (needed unless `--device-bridge none`). |
| `--serial` | An RS485 adapter for Modbus RTU, by its stable name; repeat for each. |
| `--ctid-base` | Container numbers (default 200: `bms-db` is 201, `bms-app` 202). |
| `--storage` | Container storage (default `local-lvm`). |
| `--allow-plain` | A bench only: PostgreSQL without TimescaleDB (no history retention). |

What it does:
1. Creates the private bridge `vmbrbms` and the two containers (unprivileged, started at boot, the database first).
2. Installs PostgreSQL and TimescaleDB in `bms-db`. It listens on the private bridge only, and only `bms-app` may connect.
3. Installs the release in `bms-app` (`/opt/devine-bms/<version>`, linked as `/opt/devine-bms/current`), with Mosquitto
   (no anonymous access) and the `bms-backend` service.
4. Installs the updater agent on the host.
5. Makes the secrets once:
   - the database password;
   - the Mosquitto password;
   - the updater token.

   They are kept in `/etc/devine-bms/bms.env` inside `bms-app` (readable by root and `bms` only) and in
   `/etc/devine-bms/updater.env` on the host. They are never put on a command line or in the repository.

Running it again is safe: it keeps the containers, the secrets and anything you added to `bms.env` by hand, and
installs the release again.

## 2. Make the first admin

The first-run page is only offered from the server itself. Tunnel through the host from your laptop:

```bash
ssh -L 8080:10.77.0.10:80 root@<host address>
```

Open http://localhost:8080, then enter your name, a user name and a password of at least 10 characters. Then:

- **Setup → Site:** the site's display name, time zone (a Haystack name such as `Sydney`), and its **latitude and
  longitude** (decimal degrees; south and west negative). Logic's sunrise and sunset (the astro block) need the location.
  The floor area is optional.
- **Setup → Users:** the platform admin (support) account and the customer's admin. See [Logins and users](users-and-logins.md).
- **Setup → Security:** consider **Allowed networks**, because passwords cross the LAN unencrypted (main spec 8). Include the
  tailnet range `100.64.0.0/10` if people log in remotely.

On the LAN the web app is now at `http://<the app's LAN address>/`.

## 3. Remote access

Follow [Remote access](remote-access.md): the customer's tailnet policy, `deploy/proxmox/tailscale-host.sh`, and sharing
the host with the support tailnet.

## 4. MS/TP trunks (if any)

`bms-mstp` runs on the host, one process per trunk, and is MAC 0 on every trunk. See
[`mstp/README.md`](../../mstp/README.md) for the full detail.

1. On the host:

   ```bash
   cd mstp
   sudo ./build.sh --install
   ```

2. Add the service user:

   ```bash
   sudo useradd --system --no-create-home --shell /usr/sbin/nologin --groups dialout bms-mstp
   ```

3. Copy `mstp/mstp-trunk.env.example` to `/etc/devine-bms/mstp-<trunk>.env` and fill it in:
   - the adapter's stable name, baud rate and Max_Master;
   - a unique device instance;
   - **`BMS_MSTP_LISTEN=10.77.0.1:47900`**, so `bms-app` can reach it. Use 47901 for a second trunk, and so on.
4. Install and start it:

   ```bash
   sudo cp bms-mstp@.service /etc/systemd/system/
   ```

   ```bash
   sudo systemctl daemon-reload && sudo systemctl enable --now bms-mstp@<trunk>
   ```

5. In **Setup → Buses and networks**, add the trunk (type BACnet MS/TP) with the same port, baud rate, Max_Master and
   instance, and the service address `10.77.0.1:47900`.
6. To make the trunk's devices visible to other BACnet/IP tools (the router), follow "BACnet router" in
   `mstp/README.md`. Each trunk gets a network number and its own labelled address on the device-network port.

## 5. MQTT devices (if any)

`bms-app` runs Mosquitto on port 1883 with one user, `bms`, whose password is `BMS_MQTT_PASSWORD` in `bms.env`. In
**Setup → Buses and networks**, add an MQTT broker:
- host `127.0.0.1`;
- user name `bms`;
- password variable (`passwordEnv`) `BMS_MQTT_PASSWORD`.

Each device (Tasmota, Shelly, Zigbee2MQTT and so on) gets its own login: open the broker on Setup → Buses and networks,
then **Device logins → Add login**. The BMS makes a strong password and shows it once; type it into the device's MQTT
settings straight away. **New password** and **Remove** are there too, and every change is in the audit log. The BMS
does not keep these passwords.

This uses a small root helper in `bms-app` (`/usr/local/sbin/bms-mqtt-users`, allowed for the `bms` user through sudo),
installed by `create-site.sh` and refreshed by each update. On a site built before it existed, run `create-site.sh`
again: it keeps the existing logins.

## 6. Alarm delivery

Set up the channels in **Setup → Alarm delivery** (see [Alarms](alarms.md)). The secrets go in `bms-app`'s settings
file, never in the web app:

```bash
pct exec 202 -- nano /etc/devine-bms/bms.env
```

Add, as needed:

```
BMS_SMTP_PASSWORD=...
BMS_CLICKSEND_API_KEY=...
```

Then restart the backend:

```bash
pct exec 202 -- systemctl restart bms-backend
```

Use the **Test** buttons in Setup → Alarm delivery.

## 7. The touch screen and standby

Set up the Pi as in [Pi touch screens](install-pi.md), pointing it at `http://<the app's LAN address>` (port 80 on a site
server). For a standby HMI that can take over, also add to `bms.env`:

```
BMS_HMI_URLS=http://<the Pi's address>
BMS_STANDBY_TOKEN=<the token you made>
```

Then restart `bms-backend`.

## 8. Check

- `pct exec 202 -- curl -s http://127.0.0.1/api/health` lists the devices and whether each is online.
- Setup → Buses and networks shows each connection online. Points show Good values.
- From a phone and a laptop in the customer's tailnet, `https://bms-<site>.<tailnet>.ts.net/` opens the site
  (bench test 12).
- Setup → Updates shows the updater as idle (it answers).

## Backups

Proxmox **vzdump** backups of both containers to a USB disk or NAS at the site (main spec 10). Schedule them in the
Proxmox web interface (Datacenter → Backup). The setup alone can also be exported at any time from Setup → Site.
