#!/bin/bash
# Build bms-mstp against the pinned bacnet-stack release (milestone 4c).
#
#   ./build.sh                     build into ./build
#   ./build.sh --with-test-tools   also build bacnet-stack's MS/TP test server (bacserv) and
#                                  BACnet/IP tools for router tests (bacrp, bacwir)
#   sudo ./build.sh --install      build, then install /usr/local/bin/bms-mstp
#
# Needs git, make and a C compiler (Debian: sudo apt install git build-essential).
set -euo pipefail

BACNET_STACK_TAG="bacnet-stack-1.6.1"
BACNET_STACK_COMMIT="7a53f0a72a92de1621d30fc63703a29afe12bfaf"  # checked: the tag must point here
BACNET_STACK_URL="https://github.com/bacnet-stack/bacnet-stack.git"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$HERE/build"
SRC="$BUILD/bacnet-stack"
TEST_TOOLS=0
INSTALL=0

for arg in "$@"; do
    case "$arg" in
        --with-test-tools) TEST_TOOLS=1 ;;
        --install) INSTALL=1 ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

mkdir -p "$BUILD"
if [ ! -d "$SRC/.git" ]; then
    echo "==> Fetching $BACNET_STACK_TAG"
    git clone --quiet --depth 1 --branch "$BACNET_STACK_TAG" "$BACNET_STACK_URL" "$SRC"
fi
ACTUAL="$(git -C "$SRC" rev-parse HEAD)"
if [ "$ACTUAL" != "$BACNET_STACK_COMMIT" ]; then
    echo "ERROR: $BACNET_STACK_TAG is at $ACTUAL, expected $BACNET_STACK_COMMIT. Not building." >&2
    exit 1
fi

echo "==> Building the bacnet-stack library for MS/TP and BACnet/IP (bms-mstp routes, milestone 8)"
# BACDL_NONE: bms-mstp supplies the datalink functions itself and routes between the two ports.
make -s -B BACDL=bip-mstp BACDL_DEFINE=-DBACDL_NONE=1 -C "$SRC/apps" lib

echo "==> Building bms-mstp"
rm -rf "$SRC/apps/bms-mstp"
cp -r "$HERE/bms-mstp" "$SRC/apps/bms-mstp"
make -s BACDL=bip-mstp BACDL_DEFINE=-DBACDL_NONE=1 -C "$SRC/apps" -f Makefile -f "$HERE/bms-mstp.mk" bms-mstp
install -m 0755 "$SRC/apps/bms-mstp/bms-mstp" "$BUILD/bms-mstp"

if [ "$TEST_TOOLS" -eq 1 ]; then
    # Each test tool has one datalink, so the library is rebuilt for it.
    echo "==> Building bacnet-stack's MS/TP test server (bacserv)"
    make -s -B BACDL=mstp -C "$SRC/apps" lib
    make -s -B BACDL=mstp -C "$SRC/apps" server
    install -m 0755 "$SRC/apps/server/bacserv" "$BUILD/bacserv"
    echo "==> Building bacnet-stack's BACnet/IP tools for router tests (bacrp, bacwir)"
    make -s -B BACDL=bip -C "$SRC/apps" lib
    make -s -B BACDL=bip -C "$SRC/apps" readprop
    make -s BACDL=bip -C "$SRC/apps" -f Makefile -f "$HERE/bms-mstp.mk" whoisrouter-tool
    install -m 0755 "$SRC/apps/readprop/bacrp" "$BUILD/bacrp"
    install -m 0755 "$SRC/apps/whoisrouter/bacwir" "$BUILD/bacwir"
fi

"$BUILD/bms-mstp" --version
if [ "$INSTALL" -eq 1 ]; then
    [ "$(id -u)" -eq 0 ] || { echo "ERROR: --install needs root (sudo)." >&2; exit 1; }
    install -m 0755 "$BUILD/bms-mstp" /usr/local/bin/bms-mstp
    echo "Installed /usr/local/bin/bms-mstp"
fi
