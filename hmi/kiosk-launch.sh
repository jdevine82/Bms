#!/bin/sh
# Started by bms-kiosk.service inside cage (milestone 3 spec 5).
# Opens the local waiting page, which moves to <server>/hmi once the site server answers.
set -eu

# shellcheck source=/dev/null
. /etc/devine-bms/hmi.env

if command -v chromium >/dev/null 2>&1; then
    BROWSER=chromium
else
    BROWSER=chromium-browser
fi

# A fresh profile on every start: nothing to corrupt on power loss, no restore prompts.
PROFILE="${XDG_RUNTIME_DIR:-/tmp}/bms-kiosk-profile"
rm -rf "$PROFILE"

exec "$BROWSER" \
    --kiosk \
    --ozone-platform=wayland \
    --user-data-dir="$PROFILE" \
    --incognito \
    --no-first-run \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --disable-features=Translate,TouchpadOverscrollHistoryNavigation \
    --overscroll-history-navigation=0 \
    --disable-pinch \
    --password-store=basic \
    --check-for-update-interval=31536000 \
    "file:///opt/devine-bms/hmi/waiting.html?server=${BMS_SERVER_URL}"
