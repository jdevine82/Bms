#!/bin/bash
# Install a release side by side and switch to it (milestone 12b). Runs inside bms-app, from the
# release itself, so each release brings its own install steps:
#   install-release.sh /opt/devine-bms/releases/devine-bms-0.2.0.tar.gz [--no-restart]
# Installs into /opt/devine-bms/<version>, points /opt/devine-bms/current at it and restarts
# bms-backend (migrations run at its start). Keeps the last 3 versions, never removing the one in
# use or the one before it. Prints "installed <version> (previous <version>)". Rollback is the
# host's job (a Proxmox snapshot, deploy/updater).
set -euo pipefail
TARBALL="${1:?usage: install-release.sh TARBALL [--no-restart]}"
RESTART=1
[ "${2:-}" = --no-restart ] && RESTART=0
BASE="${BMS_BASE:-/opt/devine-bms}"
PIP_INSTALL="${BMS_PIP_INSTALL:-pip}"   # tests replace the venv install
step() { echo "==> [install] $*"; }
fail() { echo "ERROR: [install] $*" >&2; exit 1; }

[ -f "$TARBALL" ] || fail "$TARBALL is missing"
TOP="$(tar -tzf "$TARBALL" | head -1 | cut -d/ -f1)"
[[ "$TOP" =~ ^devine-bms-[0-9A-Za-z.+-]+$ ]] || fail "not a release (top folder $TOP)"
VERSION="$(tar -xzf "$TARBALL" -O "$TOP/VERSION" | tr -d '[:space:]')"
[[ "$VERSION" =~ ^[0-9A-Za-z.+-]+$ ]] || fail "the release has no VERSION"
DEST="$BASE/$VERSION"
PREVIOUS="$(readlink "$BASE/current" 2>/dev/null | xargs -r basename || true)"

step "unpacking $VERSION"
rm -rf "$DEST.new"
install -d "$DEST.new"
tar -xzf "$TARBALL" -C "$DEST.new" --strip-components=1 --no-same-owner
[ -f "$DEST.new/web/dist/index.html" ] || fail "the release has no built web app"
[ -f "$DEST.new/backend/pyproject.toml" ] || fail "the release has no backend"

step "Python environment"
if [ "$PIP_INSTALL" = pip ]; then
    python3 -m venv "$DEST.new/venv"
    "$DEST.new/venv/bin/pip" install --quiet --upgrade pip
    "$DEST.new/venv/bin/pip" install --quiet "$DEST.new/backend"
else
    $PIP_INSTALL "$DEST.new"
fi
if [ "$PREVIOUS" = "$VERSION" ]; then
    # Reinstalling the version in use: move it aside first so the link never points at nothing.
    rm -rf "$DEST.old"; mv "$DEST" "$DEST.old"; mv "$DEST.new" "$DEST"; rm -rf "$DEST.old"
else
    rm -rf "$DEST"; mv "$DEST.new" "$DEST"
fi
ln -sfn "$DEST" "$BASE/current.new" && mv -T "$BASE/current.new" "$BASE/current"

step "keeping the last 3 versions"
mapfile -t ALL < <(find "$BASE" -mindepth 1 -maxdepth 1 -type d -regex '.*/[0-9][0-9A-Za-z.+-]*' -printf '%T@ %f\n' \
    | sort -rn | awk '{print $2}')
n=0
for v in "${ALL[@]}"; do
    n=$((n + 1))
    if [ "$n" -gt 3 ] && [ "$v" != "$VERSION" ] && [ "$v" != "$PREVIOUS" ]; then rm -rf "${BASE:?}/$v"; fi
done

# Root helpers that come with the release (site server only: where Mosquitto runs).
if [ "${BMS_INSTALL_HELPERS:-1}" = 1 ] && [ "$(id -u)" = 0 ] && command -v mosquitto_passwd >/dev/null \
        && [ -f "$DEST/deploy/containers/bms-mqtt-users" ]; then
    install -m 0755 "$DEST/deploy/containers/bms-mqtt-users" /usr/local/sbin/bms-mqtt-users
fi

if [ "$RESTART" = 1 ] && command -v systemctl >/dev/null && systemctl cat bms-backend.service >/dev/null 2>&1; then
    step "restarting bms-backend"
    systemctl restart bms-backend.service
fi
echo "installed $VERSION (previous ${PREVIOUS:-none})"
