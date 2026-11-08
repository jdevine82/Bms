#!/bin/bash
# Standalone mode on a Pi (milestone 11a): called by ../provision.sh --mode standalone.
# Also an HMI in standby (milestone 11, PI_MODE=standby): called by ../provision.sh --standby; the
# same backend and PostgreSQL in RAM, with the site from the server's cache.
# Installs the backend and web app from this repository, PostgreSQL in RAM, the helpers the
# backend may run as root, the config partition, the first-boot network and the read-only root.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
SKIP_PACKAGES="${SKIP_PACKAGES:-0}"
READONLY="${READONLY:-1}"
PI_MODE="${PI_MODE:-standalone}"
step() { echo "==> $*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }

[ -f "$REPO/web/dist/index.html" ] || fail "build the web app first on a computer (cd web && npm ci && npm run build), then copy the repository to the Pi"

if [ "$SKIP_PACKAGES" -eq 0 ]; then
    step "Installing PostgreSQL, Python and NetworkManager"
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        postgresql python3 python3-venv network-manager rsync
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
    || fail "the backend needs Python 3.12 or newer (found $(python3 --version)); use the current Raspberry Pi OS (64-bit)"

step "Creating the bms user"
id bms >/dev/null 2>&1 || useradd --system --home-dir /var/lib/devine-bms --shell /usr/sbin/nologin bms
for group in dialout plugdev; do getent group "$group" >/dev/null && usermod -aG "$group" bms; done

step "Installing the backend and web app (/opt/devine-bms/<version>, linked as current; milestone 12b)"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO/backend/pyproject.toml" | head -1)"
[ -n "$VERSION" ] || fail "no version in backend/pyproject.toml"
DEST="/opt/devine-bms/$VERSION"
install -d -m 0755 "$DEST" "$DEST/web" "$DEST/deploy"
rsync -a --delete --exclude node_modules --exclude .venv --exclude '__pycache__' \
    "$REPO/backend" "$REPO/config" "$DEST/"
rsync -a --delete "$REPO/web/dist/" "$DEST/web/dist/"
rsync -a --delete "$REPO/deploy/containers" "$DEST/deploy/"
echo "$VERSION" > "$DEST/VERSION"
[ -x "$DEST/venv/bin/python" ] || python3 -m venv "$DEST/venv"
"$DEST/venv/bin/pip" install --quiet --upgrade pip
"$DEST/venv/bin/pip" install --quiet "$DEST/backend"
ln -sfn "$DEST" /opt/devine-bms/current
rm -rf /opt/devine-bms/app /opt/devine-bms/venv  # the layout before milestone 12b

step "PostgreSQL in RAM (the SD card copy of PostgreSQL is not used)"
systemctl disable --now postgresql >/dev/null 2>&1 || true
install -m 0755 "$HERE/bms-pgram" "$HERE/bms-state-rw" "$HERE/bms-usb" "$HERE/bms-net" "$HERE/bms-pi-update" \
    /usr/local/sbin/
install -m 0644 "$HERE/bms-pgram.service" "$HERE/bms-backend.service" "$HERE/bms-pi-update.service" \
    /etc/systemd/system/

step "Settings (/etc/devine-bms/standalone.env)"
install -d -m 0755 /etc/devine-bms
if [ ! -f /etc/devine-bms/standalone.env ]; then
    PW="$(head -c 24 /dev/urandom | base64 | tr -dc A-Za-z0-9 | head -c 24)"
    SUPER="$(head -c 24 /dev/urandom | base64 | tr -dc A-Za-z0-9 | head -c 24)"
    cat > /etc/devine-bms/standalone.env <<ENV
BMS_MODE=$PI_MODE
BMS_PG_PASSWORD=$PW
BMS_PG_SUPER_PASSWORD=$SUPER
BMS_DATABASE_URL=postgresql+asyncpg://bms:$PW@localhost:5432/bms
BMS_ALLOW_PLAIN_POSTGRES=true
BMS_LISTEN_HOST=0.0.0.0
BMS_WEB_DIST=/opt/devine-bms/current/web/dist
BMS_CONFIG_PATH=/var/lib/devine-bms/config/site.json
BMS_STATE_FILE=/var/lib/devine-bms/config/site-state.json
BMS_STATE_RW_CMD=sudo -n /usr/local/sbin/bms-state-rw rw
BMS_STATE_RO_CMD=sudo -n /usr/local/sbin/bms-state-rw ro
BMS_USB_PATH=/run/devine-bms/usb
BMS_USB_MOUNT_CMD=sudo -n /usr/local/sbin/bms-usb mount
BMS_USB_UMOUNT_CMD=sudo -n /usr/local/sbin/bms-usb umount
BMS_NET_CMD=sudo -n /usr/local/sbin/bms-net
ENV
fi
if [ "$PI_MODE" = standby ]; then
    # Rewritten each time: the server, this HMI's name, its ports and the token may change.
    sed -i '/^BMS_MODE=/d;/^BMS_SERVER_URL=/d;/^BMS_HMI_NAME=/d;/^BMS_PI_PORTS=/d;/^BMS_STANDBY_TOKEN=/d;/^BMS_STANDBY_CACHE=/d' \
        /etc/devine-bms/standalone.env
    {
        echo "BMS_MODE=standby"
        echo "BMS_SERVER_URL=$SERVER"
        echo "BMS_HMI_NAME=$HMI_NAME"
        echo "BMS_PI_PORTS=$PI_PORTS"
        echo "BMS_STANDBY_CACHE=/var/lib/devine-bms/config/standby-cache.json"
        echo "BMS_STANDBY_TOKEN=$BMS_STANDBY_TOKEN"
        echo "# With MS/TP trunks: start and stop the Pi's own bms-mstp (MAC 1) on takeover and failback, e.g."
        echo "# BMS_MSTP_START_CMD=sudo -n /usr/bin/systemctl start bms-mstp@trunk1"
    } >> /etc/devine-bms/standalone.env
fi
# Milestone 12b: the current layout and updates from the web app (kept up to date on every run).
sed -i -e 's|^BMS_WEB_DIST=.*|BMS_WEB_DIST=/opt/devine-bms/current/web/dist|' \
    -e '/^BMS_PI_UPDATE_CMD=/d' -e '/^BMS_UPDATE_DIR=/d' /etc/devine-bms/standalone.env
{
    echo "BMS_PI_UPDATE_CMD=sudo -n /usr/local/sbin/bms-pi-update stage"
    echo "BMS_UPDATE_DIR=/var/lib/devine-bms/config/updates"
} >> /etc/devine-bms/standalone.env
chown root:bms /etc/devine-bms/standalone.env
chmod 0640 /etc/devine-bms/standalone.env
cat > /etc/sudoers.d/devine-bms <<'SUDO'
# Devine BMS standalone: the only things the backend may do as root.
bms ALL=(root) NOPASSWD: /usr/local/sbin/bms-state-rw, /usr/local/sbin/bms-usb, /usr/local/sbin/bms-net, /usr/local/sbin/bms-pi-update stage *
SUDO
chmod 0440 /etc/sudoers.d/devine-bms
visudo -cf /etc/sudoers.d/devine-bms >/dev/null

step "The config partition (BMSCONF), read-only except while saving"
install -d -m 0755 /var/lib/devine-bms/config
if [ -e /dev/disk/by-label/BMSCONF ]; then
    grep -q 'LABEL=BMSCONF' /etc/fstab || \
        echo 'LABEL=BMSCONF /var/lib/devine-bms/config ext4 ro,noatime,nofail 0 2' >> /etc/fstab
    mountpoint -q /var/lib/devine-bms/config || mount /var/lib/devine-bms/config
    mount -o remount,rw /var/lib/devine-bms/config
    chown bms:bms /var/lib/devine-bms/config
    mount -o remount,ro /var/lib/devine-bms/config
elif [ "$READONLY" -eq 1 ]; then
    fail "no partition labelled BMSCONF. Make a small ext4 partition (64 MB is plenty) labelled BMSCONF on the SD card (see hmi/README.md), or run with --no-readonly for a bench Pi"
else
    chown bms:bms /var/lib/devine-bms/config  # bench: on the root file system, always writable
    sed -i '/^BMS_STATE_R[WO]_CMD=/d' /etc/devine-bms/standalone.env
fi

if [ "$PI_MODE" = standalone ]; then
step "First-boot network: DHCP, plus the fallback address 192.168.50.10"
IFACE="$(nmcli -t -f DEVICE,TYPE device | awk -F: '$2=="ethernet"{print $1; exit}')"
if [ -n "$IFACE" ] && ! nmcli -t connection show bms-wired >/dev/null 2>&1; then
    nmcli connection add type ethernet ifname "$IFACE" con-name bms-wired ipv4.method auto \
        ipv4.addresses 192.168.50.10/24 ipv4.dhcp-timeout 30 ipv4.may-fail yes connection.autoconnect yes >/dev/null
fi
fi  # standby: the Pi keeps the network it has as the server's HMI

step "Logs in RAM"
install -d -m 0755 /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=32M\n' > /etc/systemd/journald.conf.d/devine-bms.conf

systemctl daemon-reload
systemctl enable bms-pgram.service bms-backend.service bms-pi-update.service

if [ "$READONLY" -eq 1 ]; then
    step "Read-only root file system (overlay; undo with: sudo raspi-config nonint disable_overlayfs)"
    if command -v raspi-config >/dev/null; then
        raspi-config nonint enable_overlayfs
    else
        echo "raspi-config not found: turn on the overlay file system yourself" >&2
    fi
fi
