# Installing a Pi touch screen

For installers. A Raspberry Pi with a touch screen is used in one of three ways:

| Role | What runs on the Pi | `provision.sh` |
|---|---|---|
| **Touch screen** of a site server | Only the kiosk: it shows the server's HMI. | `--server http://<server>` |
| **Standby** touch screen | The kiosk, and a copy of the backend that takes over if the server fails. | `--server http://<server> --standby` |
| **Standalone** | The whole BMS: database in RAM, backend, kiosk. No site server. | `--mode standalone` |

The scripts are in `hmi/`; [`hmi/README.md`](../../hmi/README.md) has more detail and the bench checks.

## What you need

- **Raspberry Pi 4 or 5** with **Raspberry Pi OS Lite (64-bit)**, flashed with Raspberry Pi Imager. In Imager's settings,
  set a user, the network and SSH. 32-bit is refused.
- **The touch screen.** The layout is tested at 1024 × 600 and 1280 × 720. The official Touch Display 2 starts in
  portrait: add `video=DSI-1:720x1280@60,rotate=90` (or `270`) to the end of the line in `/boot/firmware/cmdline.txt`.
- **For standby and standalone:**
  - a small extra ext4 partition labelled **`BMSCONF`** on the SD card (below);
  - a copy of the repository with the web app built;
  - internet during the install.

## Make the BMSCONF partition (standby and standalone)

The root file system is made read-only, so power cuts cannot corrupt it. The site's saved state lives on its own small
partition, written only while a change is saved. Before the first boot, on a Linux computer (the card is `/dev/sdX`;
check with `lsblk`):

1. Shrink the second (root) partition by about 64 MB with GParted.
2. Make the new partition and its file system:

   ```bash
   sudo parted /dev/sdX --script mkpart primary ext4 -64MiB 100%
   ```

   ```bash
   sudo mkfs.ext4 -L BMSCONF /dev/sdX3
   ```

On a bench Pi you can skip this and add `--no-readonly`: the root then stays writable.

## A touch screen for a site server

Copy the `hmi` folder to the Pi, then on the Pi:

```bash
cd hmi
sudo ./provision.sh --server http://<the app's LAN address>
```

```bash
sudo reboot
```

Use port 80 on a site server (no `:8000`). The Pi boots to "Connecting to the site server", then the HMI, within a
minute.

**In the web app, Setup → Security → HMI kiosks**, add the Pi's address. The screen then shows the site without anyone
logging in (view only). Commands on it need a person's PIN; see [Logins and users](users-and-logins.md).

Run `provision.sh` again to change the server address. `sudo ./uninstall.sh` removes the kiosk.

## A standby touch screen (takes over if the server fails)

The Pi keeps a copy of the server's setup, and in standby it reads and sends nothing on any bus. If the server has not
answered for 30 seconds and every serial line it shares has been quiet for 60 seconds, the Pi **takes over** (direct
mode): it reads and commands the devices itself, and raises a Critical alarm. When the server is back and has been
healthy for 5 minutes, an operator hands control back on the touch screen.

1. **Make the shared token** on any computer:

   ```bash
   openssl rand -base64 32
   ```

2. **On the server**, add to `/etc/devine-bms/bms.env` in `bms-app` (see [Site server](install-site-server.md), step 7):

   ```
   BMS_HMI_URLS=http://<the Pi's address>
   BMS_STANDBY_TOKEN=<the token>
   ```

   Then restart `bms-backend`.
3. **On the Pi**, with the repository copied and the web app built, run the command below and paste the token when asked:

   ```bash
   cd devine-bms/hmi
   sudo ./provision.sh --server http://<server> --standby --name hmi-plant --ports "rs485_1=/dev/ttyBMS-rs485-1"
   ```

   ```bash
   sudo reboot
   ```

   - `--name` is the HMI's name in the alarm and the audit log.
   - `--ports` maps each serial bus (by its name in the setup) to the Pi's own adapter.
4. **MS/TP trunks:** the Pi needs its own `bms-mstp` as **MAC 1** on each trunk. Build it on the Pi (`mstp/build.sh
   --install`) and give it an env file like the server's, with `BACNET_MSTP_MAC=1`. Do **not** enable it at boot. In
   `/etc/devine-bms/standalone.env`, set `BMS_MSTP_START_CMD` and `BMS_MSTP_STOP_CMD` (for example
   `sudo -n /usr/bin/systemctl start bms-mstp@trunk1`), and allow those two commands for the `bms` user in sudoers.
5. **Wiring:** each Pi adapter is a node on the daisy-chained trunk (cable in and out), never a long stub (main spec 9).
6. **Check:** `curl http://<pi>/api/standby/state` shows:
   - `"mode": "standby"`, `"serverOk": true` and a `cacheRevision`;
   - each serial bus, with `"clear": false` while the server polls it.

   Then test it: stop the server; after about 30 seconds plus the bus quiet time, the screen moves to the Pi's HMI with a red
   **Direct mode** banner. Start the server, wait 5 minutes, and use **Server → Hand control back** (with a PIN).

## A standalone BMS

The Pi is the whole system, sized for about 50 points. The database is in RAM; the setup, accounts, schedules and
logic are saved to the `BMSCONF` partition after each change. The log keeps 72 hours in RAM, one sample a minute, and can
export to a USB stick every hour. After a power cut, hand modes return to Auto and the log and alarm history are gone
(main spec 3.1).

1. Copy the repository (with the web app built) to the Pi and run:

   ```bash
   cd devine-bms/hmi
   sudo ./provision.sh --mode standalone
   ```

   ```bash
   sudo reboot
   ```

2. **First boot:** the screen shows **Set up this BMS** with the Pi's address, a QR code and a **setup code**.
   - On a laptop or phone on the same network, open the address, enter the code and make the first admin.
   - Without DHCP, use the fallback address: set the laptop to `192.168.50.20/24` and open `http://192.168.50.10/`.
3. **In the web app:**
   - **Setup → Site:** the name and time zone.
   - **Setup → Standalone:** the network (DHCP or a fixed address; saving it drops the fallback address), the log window and
     the USB export.
   - Then add devices as on a server ([Commissioning](commissioning.md)).
4. **USB stick:** FAT or ext4; label it `BMSLOG` if more than one USB disk is plugged in. A monthly CSV file grows every
   hour; the touch screen warns when the stick is missing.

To change the software by hand, make the root writable first:

```bash
sudo raspi-config nonint disable_overlayfs && sudo reboot
```

Afterwards, turn the overlay back on:

```bash
sudo raspi-config nonint enable_overlayfs && sudo reboot
```

Normally use **Setup → Updates** instead ([Updates](updates.md)).

**Moving up to a site server:** the state file `/var/lib/devine-bms/config/site-state.json` holds the setup in the export
format. Import it on the new server (Setup → Site → import).

## Logs on a Pi

- The kiosk:

  ```bash
  journalctl -u bms-kiosk -b
  ```

- The backend (standby and standalone):

  ```bash
  journalctl -u bms-backend -u bms-pgram -b
  ```

- Updates:

  ```bash
  journalctl -u bms-pi-update
  ```

- A text console: Ctrl+Alt+F2 with a USB keyboard; Ctrl+Alt+F1 returns to the HMI.
