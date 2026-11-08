#!/bin/bash
# Make a release tarball for site servers (milestone 12a): the backend, the built web app, config/
# and the container scripts (files under version control only; commit before building). Run on a development machine from the repository:
#   deploy/build-release.sh [out-dir]      -> devine-bms-<version>.tar.gz (version from the backend)
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$REPO/dist}"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO/backend/pyproject.toml" | head -1)"
[ -n "$VERSION" ] || { echo "ERROR: no version in backend/pyproject.toml" >&2; exit 1; }
[ -f "$REPO/web/dist/index.html" ] || { echo "ERROR: build the web app first: (cd web && npm ci && npm run build)" >&2; exit 1; }
if [ "${ALLOW_DIRTY:-0}" != 1 ] && [ -n "$(git -C "$REPO" status --porcelain -- backend config deploy/containers web/src)" ]; then
    echo "ERROR: commit first: a release is built from committed files only (ALLOW_DIRTY=1 to test with staged files)" >&2
    git -C "$REPO" status --short -- backend config deploy/containers web/src >&2
    exit 1
fi
NAME="devine-bms-$VERSION"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/$NAME" "$OUT"
# Only files under version control (never a local site.json or .env), plus the built web app.
git -C "$REPO" ls-files -z backend config deploy/containers | grep -zv '^backend/tests/' \
    | tar -C "$REPO" --null -T - -cf - | tar -C "$STAGE/$NAME" -xf -
tar -C "$REPO" -cf - web/dist | tar -C "$STAGE/$NAME" -xf -
git -C "$REPO" rev-parse HEAD > "$STAGE/$NAME/COMMIT" 2>/dev/null || echo unknown > "$STAGE/$NAME/COMMIT"
echo "$VERSION" > "$STAGE/$NAME/VERSION"
# No secrets: refuse to pack an .env file.
if find "$STAGE/$NAME" -name '.env' -o -name '*.env' | grep -q .; then
    echo "ERROR: an .env file would be packed:" >&2; find "$STAGE/$NAME" -name '.env' -o -name '*.env' >&2; exit 1
fi
tar -czf "$OUT/$NAME.tar.gz" -C "$STAGE" "$NAME"
echo "$OUT/$NAME.tar.gz"
