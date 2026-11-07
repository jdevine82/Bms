# Devine BMS Pi HMI kiosk

Milestone 3 spec: `../docs/specs/m3-hmi-kiosk.md`. Full mode: the Pi shows the HMI served by the site server at `/hmi`.
**Standalone mode** (the Pi is the whole system, milestone 11a): see "Standalone mode" below and `../docs/specs/m11a-standalone.md`.
**Standby** (the Pi takes over when the server is down, milestone 11): see "Standby takeover" below and `../docs/specs/m11-hmi-takeover.md`.

## What you need

- Raspberry Pi 4 or Pi 5 with **Raspberry Pi OS Lite (64-bit)** (32-bit is not supported; `provision.sh` refuses it).
- A 7 inch touch screen: the layout is tested at **1024 x 600** and **1280 x 720** (screen not chosen yet).
- Network to the site server, and the server running the backend with the built web app (`BMS_WEB_DIST`).

## Set up

1. Flash **Raspberry Pi OS Lite (64-bit)** with Raspberry Pi Imager. In Imager's settings, set a user, Wi-Fi or Ethernet, and turn on SSH.
2. Copy this `hmi` folder to the Pi (for example with `scp -r hmi pi@<pi address>:`), then on the Pi:

   ```bash
   cd hmi
   sudo ./provision.sh --server http://<site server address>:8000
   sudo reboot
   ```

3. The Pi boots to a "Connecting to the site server" page, then the HMI.

Run `provision.sh` again to change the server address. `sudo ./uninstall.sh` removes the kiosk.

## Screen rotation

- **Official Raspberry Pi Touch Display 2** starts in portrait. For landscape, add `video=DSI-1:720x1280@60,rotate=90`
  (or `rotate=270`, depending on how it is mounted) to the end of the single line in `/boot/firmware/cmdline.txt`, then reboot.
- **1024 x 600 screens** are usually landscape already; follow the maker's instructions if not.

Both are **[Assumed]** until the screen is chosen and checked on the bench.

## Checks on the bench (spec section 6)

1. Fresh install, reboot: the HMI appears with no login prompt or desktop, within 60 seconds of power on.
2. `sudo pkill chromium`: the HMI is back within 10 seconds.
3. Stop the site server: the "cannot be reached" banner appears; start it: values return without touching the Pi.
4. Power off with the server down, power on: the waiting page shows, then the HMI once the server is up.
5. Watchdog: `echo c | sudo tee /proc/sysrq-trigger` hangs the kernel; the Pi must reboot by itself into the HMI.
   First check the watchdog device exists: `ls /dev/watchdog`.
6. On 32-bit Raspberry Pi OS, `provision.sh` refuses to install.

## Troubleshooting

- Kiosk logs: `journalctl -u bms-kiosk -b`
- Switch to a text console: Ctrl+Alt+F2 (with a USB keyboard); back to the HMI: Ctrl+Alt+F1.
- Server address in use: `cat /etc/devine-bms/hmi.env`

## Standby takeover (the Pi takes over when the server is down)

Milestone 11, main spec 9. The Pi also runs the backend (as in standalone: PostgreSQL in RAM, the `BMSCONF`
partition, a read-only root) in **standby**: it keeps a copy of the server's setup, polls nothing and sends nothing on
any bus, and listens to its serial lines. When the server has not answered for 30 seconds and every serial line is
quiet, it takes over (**direct mode**): the screen moves to the Pi's own HMI with a red banner and a Critical alarm, and
people can view and command with their PIN. When the server is back and has been healthy for 5 minutes, an operator
taps **Hand control back** on the Server screen; the Pi releases its hand modes, stops, and sends its log to the server.

```bash
openssl rand -base64 32    # on the server: the standby token; put it in the server's environment as BMS_STANDBY_TOKEN
```

```bash
sudo ./provision.sh --server http://<server>:8000 --standby --name hmi-plant --ports "rs485_1=/dev/ttyBMS-rs485-1"
```

The token is asked for (it is kept only in `/etc/devine-bms/standalone.env`, readable by root and the `bms` user).
Then on the server set `BMS_HMI_URLS=http://<this Pi's address>` and restart it. Check with
`curl http://<pi>/api/standby/state`: `"mode": "standby"`, `"serverOk": true` and a `cacheRevision`.
With MS/TP trunks, the Pi needs its own `bms-mstp` as MAC 1 (`../mstp/README.md`), started and stopped through
`BMS_MSTP_START_CMD` and `BMS_MSTP_STOP_CMD`. More in `../docs/specs/m11-hmi-takeover.md`, "How to set up".

## Standalone mode (the Pi is the whole system)

Milestone 11a, main spec 3.1. The Pi runs the backend, PostgreSQL in RAM and the touch screen. Nothing is written to
the SD card in normal running: the site is kept in one state file on a small partition labelled `BMSCONF`, which is
read-only except while a change is saved. The log (72 hours, one sample a minute) is in RAM and can be exported to a
USB stick every hour.

### What you need

- A Raspberry Pi 5 (proposed) with **Raspberry Pi OS Lite (64-bit)**, current release (Python 3.12 or newer), and the
  touch screen.
- A copy of this repository with the web app built (on a computer: `cd web && npm ci && npm run build`).
- For MS/TP: a USB RS485 adapter and `mstp/build.sh --install` on the Pi (see `../mstp/README.md`).
- Optional: a USB stick (FAT or ext4; label it `BMSLOG` if more than one USB disk is plugged in).

### Make the config partition

The state file lives on its own small ext4 partition labelled `BMSCONF`. After flashing the card with Raspberry Pi
Imager, and **before** the first boot, on a Linux computer (the card is `/dev/sdX` here; check with `lsblk`):

```bash
sudo parted /dev/sdX --script print
```

Shrink the root partition (the second one) by about 64 MB with GParted, then in the free space at the end make an
ext4 partition labelled `BMSCONF`:

```bash
sudo parted /dev/sdX --script mkpart primary ext4 -64MiB 100%
sudo mkfs.ext4 -L BMSCONF /dev/sdX3
```

(For a bench Pi you can skip this and run `provision.sh` with `--no-readonly`: the state file is then on the root
file system, which stays writable.)

### Install

```bash
scp -r devine-bms pi@<pi address>:
ssh pi@<pi address>
cd devine-bms/hmi
sudo ./provision.sh --mode standalone
sudo reboot
```

It installs PostgreSQL (in RAM at every boot, `bms-pgram.service`), the backend (`bms-backend.service`, port 80), the
kiosk on `http://localhost/hmi`, helpers the backend may run as root through `sudo` (`/usr/local/sbin/bms-state-rw`,
`bms-usb`, `bms-net`; nothing else), a NetworkManager profile `bms-wired` (DHCP plus the fallback address
`192.168.50.10/24`), logs in RAM, and the read-only root file system (overlay).

### First boot

The screen shows **Set up this BMS** with the address, a QR code and a **setup code**. On a laptop or phone on the same
network open that address (no DHCP? set the laptop to 192.168.50.20/24 and open `http://192.168.50.10/`), enter the
setup code and make the first admin account. The screen then shows the site. Then, in the web app:

- **Setup → Site:** the site's name and time zone.
- **Setup → Standalone:** the network (DHCP or a fixed address; this also drops the fallback address), the log window
  and the USB export.
- Add devices (**Setup → Commission a device** or **Discover**) as in full mode, up to about 50 points.

### Changing the software or the read-only root

```bash
sudo raspi-config nonint disable_overlayfs && sudo reboot   # make the root writable
# ... update, run provision.sh --mode standalone again ...
sudo raspi-config nonint enable_overlayfs && sudo reboot
```

### Checks on the bench (main spec 16, tests 16 and 19)

1. First boot shows the setup page with a code; a laptop makes the admin with it; a wrong code is refused.
2. Make a change (a room), wait 3 seconds, cut the power: after boot the change is there; a hand mode is not (Auto).
3. `findmnt /` shows the overlay; `findmnt /var/lib/devine-bms/config` shows `ro` except during a save.
4. Leave a USB stick in: a monthly CSV grows every hour; pull it out: the HMI's status strip warns; put it back: the
   next export catches up.
5. Seven days with about 50 points and some logic (with PID): RAM use steady (`free -m`), no writes to the card
   (`cat /sys/block/mmcblk0/stat` before and after), polling and the web app stay responsive.

### Troubleshooting

- `journalctl -u bms-backend -u bms-pgram -b`
- The state file: `/var/lib/devine-bms/config/site-state.json` (the setup as an export under `config`, so it can be
  imported into a full-mode server: the upgrade path).
