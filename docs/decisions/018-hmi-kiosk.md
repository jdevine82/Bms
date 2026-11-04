# Pi HMI kiosk

Date: 4 Nov 2026. Status: decided.

The Pi HMI runs Raspberry Pi OS Lite (64-bit) with `cage` and Chromium in kiosk mode, started by a systemd service on tty1 that restarts it if it stops. The hardware watchdog (systemd RuntimeWatchdogSec) reboots a hung Pi. Chromium opens a local waiting page that moves to <server>/hmi once the server answers. The HMI is the site web app in an HMI layout at /hmi (dark, five bottom tabs, 44 px touch targets), tested at 1024 x 600 and 1280 x 720 because the screen is not chosen yet.

Spec: main spec 6.2, 13; docs/specs/m3-hmi-kiosk.md
