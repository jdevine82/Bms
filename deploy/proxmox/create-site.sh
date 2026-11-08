#!/bin/bash
# Build a Devine BMS site server on Proxmox VE (milestone 12a, decision 030).
#
# Two LXC containers and a private bridge:
#   bms-db  (PostgreSQL 16 + TimescaleDB)            10.77.0.20 on vmbrbms only
#   bms-app (backend + web app + Mosquitto)          10.77.0.10 on vmbrbms, the site LAN and the device network
#   the host                                         10.77.0.1 on vmbrbms (tailscale serve, bms-mstp APIs)
#
# Run as root on the Proxmox host, with a release made by deploy/build-release.sh:
#   ./create-site.sh --site plant1 --release devine-bms-0.1.0.tar.gz \
#       [--lan-bridge vmbr0] [--lan-ip dhcp|192.168.1.20/24,gw=192.168.1.1] \
#       [--device-bridge vmbr1|none] [--device-ip 192.168.20.10/24] \
#       [--serial /dev/ttyBMS-rs485-1 ...] [--ctid-base 200] [--storage local-lvm]
#       [--allow-plain]   (a bench only: PostgreSQL without TimescaleDB, no history retention)
#   DRY_RUN=1 ./create-site.sh ...    prints what it would do and changes nothing.
#
# Safe to run again: existing containers, the bridge and the secrets are kept; the release is
# installed again. Secrets are made here and written only inside bms-app (/etc/devine-bms/bms.env).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SITE=""
RELEASE=""
LAN_BRIDGE=vmbr0
LAN_IP=dhcp
DEVICE_BRIDGE=vmbr1
DEVICE_IP=""
SERIALS=()
CTID_BASE=200
STORAGE=local-lvm
TEMPLATE_STORAGE=local
PRIVATE_BRIDGE=vmbrbms
PRIVATE_NET=10.77.0
DRY_RUN="${DRY_RUN:-0}"
ALLOW_PLAIN=0

usage() { sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; }
fail() { echo "ERROR: $*" >&2; exit 1; }
step() { echo "==> $*"; }
show() {  # a dry run prints the command, quoting only arguments with spaces
    local out="+" a
    for a in "$@"; do
        if [[ "$a" =~ [[:space:]] ]]; then out+=" '$a'"; else out+=" $a"; fi
    done
    echo "$out"
}
run() {
    if [ "$DRY_RUN" = 1 ]; then show "$@"; else "$@"; fi
}

while [ $# -gt 0 ]; do
    case "$1" in
        --site) SITE="${2:-}"; shift 2 ;;
        --release) RELEASE="${2:-}"; shift 2 ;;
        --lan-bridge) LAN_BRIDGE="${2:-}"; shift 2 ;;
        --lan-ip) LAN_IP="${2:-}"; shift 2 ;;
        --device-bridge) DEVICE_BRIDGE="${2:-}"; shift 2 ;;
        --device-ip) DEVICE_IP="${2:-}"; shift 2 ;;
        --serial) SERIALS+=("${2:-}"); shift 2 ;;
        --ctid-base) CTID_BASE="${2:-}"; shift 2 ;;
        --storage) STORAGE="${2:-}"; shift 2 ;;
        --template-storage) TEMPLATE_STORAGE="${2:-}"; shift 2 ;;
        --allow-plain) ALLOW_PLAIN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

[[ "$SITE" =~ ^[a-z0-9][a-z0-9-]{0,40}$ ]] || fail "--site: lower-case letters, digits and -, as in the BMS site name"
[ -f "$RELEASE" ] || fail "--release: the tarball from deploy/build-release.sh"
[[ "$CTID_BASE" =~ ^[0-9]+$ ]] || fail "--ctid-base: a number"
[[ "$LAN_IP" == dhcp || "$LAN_IP" =~ ^[0-9.]+/[0-9]+(,gw=[0-9.]+)?$ ]] || fail "--lan-ip: dhcp or ADDRESS/PREFIX[,gw=GATEWAY]"
if [ "$DEVICE_BRIDGE" != none ]; then
    [[ "$DEVICE_IP" =~ ^[0-9.]+/[0-9]+$ ]] || fail "--device-ip: the app's address on the device network, ADDRESS/PREFIX (or --device-bridge none)"
fi
for s in "${SERIALS[@]}"; do
    [[ "$s" =~ ^/dev/ttyBMS-[A-Za-z0-9_-]+$ ]] || fail "--serial $s: use the stable names from deploy/udev (/dev/ttyBMS-...)"
done
if [ "$DRY_RUN" != 1 ]; then
    [ "$(id -u)" -eq 0 ] || fail "run as root on the Proxmox host"
    command -v pct >/dev/null || fail "pct not found: run this on a Proxmox VE host"
fi

DB_ID=$((CTID_BASE + 1))
APP_ID=$((CTID_BASE + 2))
DB_IP="$PRIVATE_NET.20"
APP_IP="$PRIVATE_NET.10"
HOST_IP="$PRIVATE_NET.1"
VERSION="$(basename "$RELEASE" .tar.gz)"

exists() { [ "$DRY_RUN" != 1 ] && pct status "$1" >/dev/null 2>&1; }

step "Private bridge $PRIVATE_BRIDGE ($HOST_IP/24, no physical port)"
if ! grep -q "^iface $PRIVATE_BRIDGE" /etc/network/interfaces 2>/dev/null; then
    if [ "$DRY_RUN" = 1 ]; then
        echo "+ append $PRIVATE_BRIDGE to /etc/network/interfaces; ifreload -a"
    else
        cat >> /etc/network/interfaces <<IFACE

# Devine BMS: app <-> database, tailscale serve -> app, bms-mstp APIs (milestone 12a)
auto $PRIVATE_BRIDGE
iface $PRIVATE_BRIDGE inet static
	address $HOST_IP/24
	bridge-ports none
	bridge-stp off
	bridge-fd 0
IFACE
        ifreload -a
    fi
fi

step "Debian 13 template"
TEMPLATE="$(pveam available --section system 2>/dev/null | awk '/debian-13-standard/ {print $2}' | sort -V | tail -1 || true)"
[ -n "$TEMPLATE" ] || TEMPLATE="debian-13-standard_13.1-2_amd64.tar.zst"
if ! pveam list "$TEMPLATE_STORAGE" 2>/dev/null | grep -q "$TEMPLATE"; then
    run pveam update
    run pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
fi
OSTEMPLATE="$TEMPLATE_STORAGE:vztmpl/$TEMPLATE"

step "bms-db (CT $DB_ID, $DB_IP)"
if exists "$DB_ID"; then
    echo "    already there: kept"
else
    run pct create "$DB_ID" "$OSTEMPLATE" --hostname "bms-db-$SITE" --unprivileged 1 --features nesting=1 \
        --cores 2 --memory 2048 --swap 512 --rootfs "$STORAGE:32" \
        --net0 "name=eth0,bridge=$PRIVATE_BRIDGE,ip=$DB_IP/24" \
        --onboot 1 --startup order=1 --tags "devine-bms;$SITE"
fi

step "bms-app (CT $APP_ID, $APP_IP)"
LAN_NET="name=eth1,bridge=$LAN_BRIDGE,ip=${LAN_IP%%,*}"
[[ "$LAN_IP" == *",gw="* ]] && LAN_NET="$LAN_NET,gw=${LAN_IP##*,gw=}"
if exists "$APP_ID"; then
    echo "    already there: kept"
else
    NETS=(--net0 "name=eth0,bridge=$PRIVATE_BRIDGE,ip=$APP_IP/24" --net1 "$LAN_NET")
    [ "$DEVICE_BRIDGE" != none ] && NETS+=(--net2 "name=eth2,bridge=$DEVICE_BRIDGE,ip=$DEVICE_IP")
    run pct create "$APP_ID" "$OSTEMPLATE" --hostname "bms-$SITE" --unprivileged 1 --features nesting=1 \
        --cores 2 --memory 2048 --swap 512 --rootfs "$STORAGE:16" "${NETS[@]}" \
        --onboot 1 --startup order=2,up=30 --tags "devine-bms;$SITE"
fi
i=0
for s in "${SERIALS[@]}"; do
    # The adapter by its stable name; gid 20 is dialout in the container (the bms user is in it).
    run pct set "$APP_ID" "--dev$i" "path=$s,gid=20,mode=0660"
    i=$((i + 1))
done

step "Starting the containers"
for id in "$DB_ID" "$APP_ID"; do
    if [ "$DRY_RUN" = 1 ] || [ "$(pct status "$id" | awk '{print $2}')" != running ]; then run pct start "$id"; fi
done

step "Secrets (made once, kept in bms-app only)"
PG_PASSWORD=""
if [ "$DRY_RUN" != 1 ] && pct exec "$APP_ID" -- test -f /etc/devine-bms/bms.env; then
    PG_PASSWORD="$(pct exec "$APP_ID" -- sed -n 's/^BMS_PG_PASSWORD=//p' /etc/devine-bms/bms.env)"
fi
[ -n "$PG_PASSWORD" ] || PG_PASSWORD="$(head -c 32 /dev/urandom | base64 | tr -dc A-Za-z0-9 | head -c 32)"

step "Installing $VERSION in both containers"
for id in "$DB_ID" "$APP_ID"; do
    run pct exec "$id" -- mkdir -p /opt/devine-bms/releases
    run pct push "$id" "$RELEASE" "/opt/devine-bms/releases/$VERSION.tar.gz"
    run pct push "$id" "$HERE/../containers/provision-$([ "$id" = "$DB_ID" ] && echo db || echo app).sh" /root/provision.sh
done
# The password goes in on standard input, never on a command line (ps, shell history).
if [ "$DRY_RUN" = 1 ]; then
    echo "+ pct exec $DB_ID -- env APP_IP=$APP_IP DB_IP=$DB_IP bash /root/provision.sh  (password on stdin)"
    echo "+ pct exec $APP_ID -- env ... bash /root/provision.sh  (password on stdin)"
else
    printf '%s\n' "$PG_PASSWORD" | pct exec "$DB_ID" -- env APP_IP="$APP_IP" DB_IP="$DB_IP" ALLOW_PLAIN="$ALLOW_PLAIN" bash /root/provision.sh
    printf '%s\n' "$PG_PASSWORD" | pct exec "$APP_ID" -- env SITE="$SITE" VERSION="$VERSION" DB_IP="$DB_IP" \
        HOST_IP="$HOST_IP" ALLOW_PLAIN="$ALLOW_PLAIN" bash /root/provision.sh
fi

echo
echo "Done. Site $SITE: bms-db is CT $DB_ID, bms-app is CT $APP_ID."
echo "Next:"
echo "  1. Remote access: $HERE/tailscale-host.sh --site $SITE   (then share this host into the support tailnet)"
echo "  2. First admin: from this host, open the first-run page through the private bridge, for example"
echo "     ssh -L 8080:$APP_IP:80 root@<this host>  and browse to http://localhost:8080"
echo "  3. MS/TP trunks: install bms-mstp on this host (mstp/README.md) with BMS_MSTP_LISTEN=$HOST_IP:47900"
