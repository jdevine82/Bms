#!/bin/bash
# bms-app: the backend (which serves the web app) and Mosquitto (milestone 12a). Run inside the
# container by deploy/proxmox/create-site.sh, with the database password on standard input.
# Installs /opt/devine-bms/releases/$VERSION.tar.gz with the release's own install-release.sh
# (/opt/devine-bms/<version>, linked as /opt/devine-bms/current). Safe to run again.
set -euo pipefail
: "${SITE:?}" "${VERSION:?}" "${DB_IP:?}" "${HOST_IP:?}"
ALLOW_PLAIN="${ALLOW_PLAIN:-0}"
step() { echo "==> [bms-app] $*"; }
fail() { echo "ERROR: [bms-app] $*" >&2; exit 1; }
IFS= read -r PG_PASSWORD || true
IFS= read -r UPDATER_TOKEN || true  # milestone 12b: the host's updater agent
[ "${#PG_PASSWORD}" -ge 20 ] || fail "the database password is missing (it comes on standard input)"

step "packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends python3 python3-venv mosquitto ca-certificates curl
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' || fail "the backend needs Python 3.12 or newer"

step "the bms user"
id bms >/dev/null 2>&1 || useradd --system --home-dir /var/lib/devine-bms --create-home --shell /usr/sbin/nologin bms
usermod -aG dialout bms

step "release $VERSION"
TARBALL="/opt/devine-bms/releases/$VERSION.tar.gz"
[ -f "$TARBALL" ] || fail "$TARBALL is missing"
# The release's own installer (the same one updates use, milestone 12b).
tar -xzf "$TARBALL" --wildcards -O '*/deploy/containers/install-release.sh' > /root/install-release.sh
bash /root/install-release.sh "$TARBALL" --no-restart

step "settings (/etc/devine-bms/bms.env; secrets stay in this file)"
install -d -m 0755 /etc/devine-bms /var/lib/devine-bms/config
chown bms:bms /var/lib/devine-bms/config
ENV=/etc/devine-bms/bms.env
MQTT_PASSWORD=""
[ -f "$ENV" ] && MQTT_PASSWORD="$(sed -n 's/^BMS_MQTT_PASSWORD=//p' "$ENV")"
[ -n "$MQTT_PASSWORD" ] || MQTT_PASSWORD="$(head -c 24 /dev/urandom | base64 | tr -dc A-Za-z0-9 | head -c 24)"
umask 027
cat > "$ENV.new" <<ENVEOF
# Devine BMS site $SITE (deploy/containers/provision-app.sh). Secrets: keep this file 0640 root:bms.
BMS_SITE_NAME=$SITE
BMS_PG_PASSWORD=$PG_PASSWORD
BMS_DATABASE_URL=postgresql+asyncpg://bms:$PG_PASSWORD@$DB_IP:5432/bms
BMS_CONFIG_PATH=/var/lib/devine-bms/config/site.json
BMS_WEB_DIST=/opt/devine-bms/current/web/dist
BMS_LISTEN_HOST=0.0.0.0
# tailscale serve runs on the Proxmox host ($HOST_IP on the private bridge).
BMS_TRUSTED_PROXIES=$HOST_IP
# Updates (milestone 12b): the updater agent on the host.
BMS_UPDATER_URL=http://$HOST_IP:8701
BMS_UPDATER_TOKEN=$UPDATER_TOKEN
# The site's Mosquitto: name this variable as passwordEnv for user bms in the MQTT broker settings.
BMS_MQTT_PASSWORD=$MQTT_PASSWORD
# Device logins on it, from Setup -> Buses and networks.
BMS_MQTT_USERS_CMD=sudo -n /usr/local/sbin/bms-mqtt-users
$([ "$ALLOW_PLAIN" = 1 ] && echo "BMS_ALLOW_PLAIN_POSTGRES=true")
ENVEOF
if [ -f "$ENV" ]; then  # keep what an installer added by hand (alarm delivery keys, standby token)
    grep -vE '^(# Devine BMS site|BMS_SITE_NAME|BMS_PG_PASSWORD|BMS_DATABASE_URL|BMS_CONFIG_PATH|BMS_WEB_DIST|BMS_LISTEN_HOST|# tailscale serve|BMS_TRUSTED_PROXIES|# Updates \(milestone 12b\)|BMS_UPDATER_URL|BMS_UPDATER_TOKEN|# The site.s Mosquitto|BMS_MQTT_PASSWORD|# Device logins on it|BMS_MQTT_USERS_CMD|BMS_ALLOW_PLAIN_POSTGRES)' \
        "$ENV" >> "$ENV.new" || true
fi
chown root:bms "$ENV.new"
chmod 0640 "$ENV.new"
mv "$ENV.new" "$ENV"
umask 022

step "Mosquitto (user bms, no anonymous access; device logins from the web app)"
cat > /etc/mosquitto/conf.d/devine-bms.conf <<MQ
# Devine BMS (deploy/containers/provision-app.sh)
listener 1883
allow_anonymous false
password_file /etc/mosquitto/devine-bms.passwd
MQ
# The helper the backend uses (through sudo) for device logins: Setup -> Buses and networks.
tar -xzf "$TARBALL" --wildcards -O '*/deploy/containers/bms-mqtt-users' > /usr/local/sbin/bms-mqtt-users
chmod 0755 /usr/local/sbin/bms-mqtt-users
cat > /etc/sudoers.d/devine-bms-mqtt <<'SUDO'
# Devine BMS: device logins on the site's Mosquitto (milestone 13 follow-up). Nothing else as root.
bms ALL=(root) NOPASSWD: /usr/local/sbin/bms-mqtt-users list, /usr/local/sbin/bms-mqtt-users add *, /usr/local/sbin/bms-mqtt-users remove *
SUDO
chmod 0440 /etc/sudoers.d/devine-bms-mqtt
visudo -cf /etc/sudoers.d/devine-bms-mqtt >/dev/null
systemctl enable --now mosquitto >/dev/null
# The BMS's own login; device logins already in the file are kept (the password is on stdin).
printf '%s\n' "$MQTT_PASSWORD" | BMS_MQTT_RELOAD_CMD="" /usr/local/sbin/bms-mqtt-users set-bms
systemctl restart mosquitto

step "the backend service"
cat > /etc/systemd/system/bms-backend.service <<UNIT
# Devine BMS backend and web app (milestone 12a)
[Unit]
Description=Devine BMS ($SITE)
After=network-online.target mosquitto.service
Wants=network-online.target

[Service]
User=bms
Group=bms
WorkingDirectory=/opt/devine-bms/current/backend
EnvironmentFile=/etc/devine-bms/bms.env
ExecStartPre=/opt/devine-bms/current/venv/bin/alembic upgrade head
ExecStart=/opt/devine-bms/current/venv/bin/uvicorn devine_bms.api.app:app --host 0.0.0.0 --port 80 --no-proxy-headers
# Port 80 without root: the web app is http://<address>/ on the LAN (main spec 8).
AmbientCapabilities=CAP_NET_BIND_SERVICE
Restart=always
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable bms-backend.service >/dev/null
systemctl restart bms-backend.service
for _ in $(seq 1 60); do
    if curl -fsS http://127.0.0.1/api/health >/dev/null 2>&1; then step "running: http://127.0.0.1/api/health answers"; exit 0; fi
    sleep 1
done
fail "the backend did not answer in 60 s: journalctl -u bms-backend"
