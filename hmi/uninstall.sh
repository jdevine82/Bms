#!/bin/bash
# Remove the Devine BMS HMI kiosk and return the Pi to the normal login prompt.
#   sudo ./uninstall.sh
# Installed packages (cage, Chromium) are left in place.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "ERROR: run as root (sudo)." >&2; exit 1; }

systemctl disable --now bms-kiosk.service >/dev/null 2>&1 || true
rm -f /etc/systemd/system/bms-kiosk.service /etc/pam.d/bms-kiosk
rm -f /etc/systemd/system.conf.d/bms-watchdog.conf
rm -rf /opt/devine-bms/hmi /etc/devine-bms/hmi.env
systemctl daemon-reload
systemctl set-default multi-user.target
systemctl enable getty@tty1.service >/dev/null 2>&1 || true
if id bms-kiosk >/dev/null 2>&1; then userdel -r bms-kiosk >/dev/null 2>&1 || true; fi

echo "Kiosk removed. Reboot to get the login prompt back:  sudo reboot"
