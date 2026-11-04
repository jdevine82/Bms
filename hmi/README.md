# Devine BMS Pi HMI kiosk

Milestone 3 spec: `../docs/specs/m3-hmi-kiosk.md`. Full mode: the Pi shows the HMI served by the site server at `/hmi`.

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
