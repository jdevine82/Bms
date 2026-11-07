#!/bin/bash
# Set up a Raspberry Pi as a Devine BMS HMI kiosk (milestone 3 spec 5), or as a standalone BMS
# (the Pi is the whole system; milestone 11a, main spec 3.1).
#
# Run once as root on a fresh Raspberry Pi OS Lite (64-bit), from this folder:
#   sudo ./provision.sh --server http://<site server address>:8000     (full mode: HMI of a site server)
#   sudo ./provision.sh --mode standalone [--no-readonly]              (standalone: from a copy of the repository)
#   sudo ./provision.sh --server http://<server>:8000 --standby [--name hmi-plant] [--ports "rs485_1=/dev/ttyBMS-rs485-1"]
#                                                                      (full mode, and the Pi can take over: milestone 11)
#   With --standby the shared standby token is asked for (or give it as BMS_STANDBY_TOKEN=... before ./provision.sh);
#   it is kept only in /etc/devine-bms/standalone.env on the Pi and in the server's environment.
#
# Safe to run again: it rewrites the same files. Remove with ./uninstall.sh.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER=""
SKIP_PACKAGES=0
MODE=full
READONLY=1
STANDBY=0
HMI_NAME="$(hostname)"
PI_PORTS=""

usage() {
    echo "Usage: sudo $0 --server http://<site server address>:8000 [--skip-packages]"
    echo "       sudo $0 --mode standalone [--no-readonly] [--skip-packages]"
    echo "       sudo $0 --server http://<site server address>:8000 --standby [--name <hmi name>] [--ports bus=port,...] [--no-readonly]"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --server) SERVER="${2:-}"; shift 2 ;;
        --skip-packages) SKIP_PACKAGES=1; shift ;;
        --mode) MODE="${2:-}"; shift 2 ;;
        --no-readonly) READONLY=0; shift ;;
        --standby) STANDBY=1; shift ;;
        --name) HMI_NAME="${2:-}"; shift 2 ;;
        --ports) PI_PORTS="${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

fail() { echo "ERROR: $*" >&2; exit 1; }
step() { echo "==> $*"; }

[ "$(id -u)" -eq 0 ] || fail "run as root (sudo)."
case "$MODE" in
    full) ;;
    standalone) SERVER="${SERVER:-http://localhost}" ;;  # the backend runs on the Pi itself
    *) fail "--mode is full or standalone" ;;
esac
[[ "$SERVER" =~ ^https?://[^[:space:]/]+(:[0-9]+)?/?$ ]] \
    || fail "give the site server address, for example --server http://192.168.10.5:8000"
SERVER="${SERVER%/}"
if [ "$STANDBY" -eq 1 ]; then
    [ "$MODE" = full ] || fail "--standby is for an HMI of a site server (full mode)"
    [[ "$HMI_NAME" =~ ^[A-Za-z0-9_.-]{1,40}$ ]] || fail "--name: letters, digits, - _ . (at most 40)"
    if [ -z "${BMS_STANDBY_TOKEN:-}" ]; then
        read -rsp "The standby token (the same as BMS_STANDBY_TOKEN on the server): " BMS_STANDBY_TOKEN; echo
    fi
    [ "${#BMS_STANDBY_TOKEN}" -ge 20 ] || fail "the standby token must be at least 20 characters"
fi

# 64-bit only (decision 013): the kernel and the userland must both be 64-bit.
ARCH="$(uname -m)"
DPKG_ARCH="$(dpkg --print-architecture 2>/dev/null || echo unknown)"
if [ "$ARCH" != "aarch64" ] || [ "$DPKG_ARCH" != "arm64" ]; then
    fail "this needs 64-bit Raspberry Pi OS (found kernel $ARCH, packages $DPKG_ARCH). Reflash the SD card with 'Raspberry Pi OS Lite (64-bit)' using Raspberry Pi Imager."
fi
[ -r /etc/os-release ] && . /etc/os-release
case "${ID:-}" in
    debian|raspbian) ;;
    *) fail "this needs Raspberry Pi OS (found ${PRETTY_NAME:-unknown})." ;;
esac

if [ "$SKIP_PACKAGES" -eq 0 ]; then
    step "Installing cage, Chromium and fonts"
    apt-get update
    if apt-cache show chromium >/dev/null 2>&1; then CHROMIUM_PKG=chromium; else CHROMIUM_PKG=chromium-browser; fi
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        cage "$CHROMIUM_PKG" fonts-dejavu-core dbus-user-session
fi

step "Creating the kiosk user"
if ! id bms-kiosk >/dev/null 2>&1; then
    useradd --system --create-home --home-dir /var/lib/bms-kiosk --shell /usr/sbin/nologin bms-kiosk
fi
passwd -l bms-kiosk >/dev/null
for group in video input render; do
    if getent group "$group" >/dev/null; then usermod -aG "$group" bms-kiosk; fi
done

step "Writing settings"
install -d -m 0755 /etc/devine-bms /opt/devine-bms/hmi
printf 'BMS_SERVER_URL=%s\n' "$SERVER" > /etc/devine-bms/hmi.env
# Milestone 11: the Pi's own backend, which takes over when the server is down.
if [ "$STANDBY" -eq 1 ]; then printf 'BMS_STANDBY_URL=http://localhost\n' >> /etc/devine-bms/hmi.env; fi
chmod 0644 /etc/devine-bms/hmi.env

step "Installing the waiting page and kiosk launcher"
install -m 0644 "$HERE/waiting.html" /opt/devine-bms/hmi/waiting.html
install -m 0755 "$HERE/kiosk-launch.sh" /opt/devine-bms/hmi/kiosk-launch.sh

step "Installing the kiosk service"
cat > /etc/pam.d/bms-kiosk <<'PAM'
# Session for bms-kiosk.service (cage needs a logind seat session).
auth     required pam_unix.so nullok
account  required pam_unix.so
session  required pam_unix.so
session  required pam_systemd.so
PAM
install -m 0644 "$HERE/bms-kiosk.service" /etc/systemd/system/bms-kiosk.service

step "Turning on the hardware watchdog (a hung Pi reboots by itself)"
install -d -m 0755 /etc/systemd/system.conf.d
cat > /etc/systemd/system.conf.d/bms-watchdog.conf <<'CONF'
[Manager]
RuntimeWatchdogSec=15s
RebootWatchdogSec=2min
CONF

step "Turning off screen blanking"
CMDLINE=""
for f in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
    if [ -f "$f" ]; then CMDLINE="$f"; break; fi
done
if [ -n "$CMDLINE" ] && ! grep -q 'consoleblank=0' "$CMDLINE"; then
    sed -i '1 s/$/ consoleblank=0/' "$CMDLINE"
fi

if [ "$MODE" = standalone ]; then
    step "Standalone mode: the backend, PostgreSQL in RAM, the config partition"
    SKIP_PACKAGES="$SKIP_PACKAGES" READONLY="$READONLY" "$HERE/standalone/setup-standalone.sh"
fi
if [ "$STANDBY" -eq 1 ]; then
    step "Standby: the Pi's own backend, which takes over when the server is down"
    SKIP_PACKAGES="$SKIP_PACKAGES" READONLY="$READONLY" PI_MODE=standby SERVER="$SERVER" HMI_NAME="$HMI_NAME" \
        PI_PORTS="$PI_PORTS" BMS_STANDBY_TOKEN="$BMS_STANDBY_TOKEN" "$HERE/standalone/setup-standalone.sh"
fi

step "Starting the kiosk at boot"
systemctl daemon-reload
systemctl set-default graphical.target
systemctl enable bms-kiosk.service
systemctl disable getty@tty1.service >/dev/null 2>&1 || true

echo
if [ "$MODE" = standalone ]; then
    echo "Done. After a reboot the screen shows the setup page: open the address it shows from a laptop"
    echo "or phone on the same network and enter the setup code. (Fallback address: http://192.168.50.10/)"
fi
if [ "$STANDBY" -eq 1 ]; then
    echo "Standby is set up. On the server, add this HMI: BMS_HMI_URLS=http://<this Pi's address>"
    echo "and the same BMS_STANDBY_TOKEN, then restart the server."
fi
echo "Done. The HMI will open $SERVER/hmi at every boot."
echo "Reboot now to start it:  sudo reboot"
