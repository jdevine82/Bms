#!/bin/bash
# Remote access for a site server (milestone 12a, decision 030): Tailscale on the Proxmox host,
# tagged for this site in the customer's tailnet, and HTTPS for the web app with tailscale serve
# (a Tailscale certificate, renewed by Tailscale) passing requests to bms-app over the private bridge.
#
#   ./tailscale-host.sh --site plant1 [--app http://10.77.0.10:80]
#   DRY_RUN=1 ./tailscale-host.sh --site plant1
#
# Login: interactive by default (Tailscale prints a URL to open as one of the tailnet's tag owners).
# For an unattended install give a one-off auth key in the environment only: TS_AUTHKEY=tskey-... ./tailscale-host.sh ...
# Before this, the tailnet's policy must name tag:bms-site-<site> (deploy/tailscale/policy.py).
set -euo pipefail
SITE=""
APP=http://10.77.0.10:80
DRY_RUN="${DRY_RUN:-0}"
fail() { echo "ERROR: $*" >&2; exit 1; }
step() { echo "==> $*"; }
show() {  # a dry run prints the command, quoting only arguments with spaces
    local out="+" a
    for a in "$@"; do
        if [[ "$a" =~ [[:space:]] ]]; then out+=" '$a'"; else out+=" $a"; fi
    done
    echo "$out"
}
run() { if [ "$DRY_RUN" = 1 ]; then show "$@"; else "$@"; fi; }

while [ $# -gt 0 ]; do
    case "$1" in
        --site) SITE="${2:-}"; shift 2 ;;
        --app) APP="${2:-}"; shift 2 ;;
        -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) fail "unknown option $1" ;;
    esac
done
[[ "$SITE" =~ ^[a-z0-9][a-z0-9-]{0,40}$ ]] || fail "--site: lower-case letters, digits and -"
[[ "$APP" =~ ^http://[0-9.]+:[0-9]+$ ]] || fail "--app: http://ADDRESS:PORT"
[ "$DRY_RUN" = 1 ] || [ "$(id -u)" -eq 0 ] || fail "run as root on the Proxmox host"
TAG="tag:bms-site-$SITE"

if ! command -v tailscale >/dev/null 2>&1; then
    step "Installing Tailscale (its own apt repository)"
    if [ "$DRY_RUN" = 1 ]; then
        echo "+ curl -fsSL https://tailscale.com/install.sh | sh"
    else
        curl -fsSL https://tailscale.com/install.sh | sh
    fi
fi
run systemctl enable --now tailscaled

step "Joining the customer's tailnet as bms-$SITE ($TAG)"
UP=(tailscale up --hostname "bms-$SITE" --advertise-tags "$TAG" --ssh=false --accept-dns=false)
if [ -n "${TS_AUTHKEY:-}" ]; then
    # tailscale reads the key from a file (file:PATH), so it is never on a command line (ps, logs).
    KEYFILE="$(mktemp)"
    trap 'rm -f "$KEYFILE"' EXIT
    printf '%s' "$TS_AUTHKEY" > "$KEYFILE"
    UP+=(--auth-key "file:$KEYFILE")
fi
run "${UP[@]}"

step "HTTPS: tailscale serve on 443 -> $APP"
run tailscale serve --bg --https=443 "$APP"

if [ "$DRY_RUN" != 1 ]; then
    NAME="$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
    echo
    echo "Done. The site is https://$NAME/ for people in the customer's tailnet whose group allows it."
fi
echo "Support: in the customer's Tailscale admin console, share this machine (bms-$SITE) with the support tailnet."
echo "Check from a phone and a laptop in the tailnet, and that a person of another site is refused (bench test 12)."
