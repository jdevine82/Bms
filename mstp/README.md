# bms-mstp: BACnet MS/TP service

Milestone 4c spec: `../docs/specs/m4c-bacnet-mstp.md`. Main spec 4, 5.1, 9 and 11.

`bms-mstp` runs on the **Proxmox host**, one process per MS/TP trunk. It owns the trunk's RS485
adapter, takes part in the token passing as an MS/TP master (MAC 0 on the site server), and gives
`bms-backend` a small local API to find and read devices. It is built on
[bacnet-stack](https://github.com/bacnet-stack/bacnet-stack) **1.6.1** (pinned by tag and commit).

It only reads. Writing comes in milestone 7.

## Build

Needs git, make and a C compiler (`sudo apt install git build-essential`).

```bash
./build.sh
```

`./build.sh --with-test-tools` also builds bacnet-stack's MS/TP test server (`bacserv`), and
`sudo ./build.sh --install` installs `/usr/local/bin/bms-mstp`.

## Install on the Proxmox host

1. Name the RS485 adapter by serial number (`../deploy/udev/99-devine-bms-rs485.rules`).
2. Add a user for the service:

   ```bash
   sudo useradd --system --no-create-home --shell /usr/sbin/nologin --groups dialout bms-mstp
   ```

3. Copy `mstp-trunk.env.example` to `/etc/devine-bms/mstp-<trunk>.env` (for example `mstp-mstp1.env`) and fill it in.
4. Install and start the service:

   ```bash
   sudo cp bms-mstp@.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now bms-mstp@mstp1
   ```

5. In the site configuration, add the trunk as a bus of type `bacnet_mstp` with the same port, baud
   rate, Max_Master and device instance, and the service's API address (`../config/site.example-mstp.json`).

Logs: `journalctl -u bms-mstp@mstp1`.

## The local API

Text request lines over TCP; one JSON answer line each, with the request's ID and `"ok"`:

| Request | Answer |
|---|---|
| `PING <id>` | service and bacnet-stack versions, this node's MAC and instance |
| `STATS <id>` | trunk counters: frames sent and received, invalid frames, lost tokens, CRC errors, poll-for-master |
| `WHOIS <id> <low> <high> <wait_ms>` | devices that answered: instance, MAC, max APDU, segmentation, vendor (`-1 -1` for all) |
| `READ <id> <mac> <type>:<instance>:<property> ...` | one ReadPropertyMultiple; a value or an error for each property |

Object types and properties are BACnet numbers: `0:1:85` is analog-input 1 present-value, and `:111` is its
status flags. A device that does not answer gives `"error": "no answer (timeout)"` after the timeout and retries.

Try it by hand:

```bash
printf 'PING p1\nSTATS s1\nWHOIS w1 -1 -1 3000\n' | nc -q 5 127.0.0.1 47900
```

## Test without hardware

Two pseudo-terminals joined by `socat` stand in for an RS485 trunk, with bacnet-stack's test server as an MS/TP device:

```bash
./build.sh --with-test-tools
cd ../backend
BMS_MSTP_BIN=../mstp/build/bms-mstp BMS_BACSERV_BIN=../mstp/build/bacserv uv run pytest tests/test_mstp.py
```

## Licence

bacnet-stack's library is **GPL-2.0-or-later WITH GCC-exception-2.0**: it can be linked into proprietary
code without that code becoming GPL. Changes to bacnet-stack's own files must be shared if they are
distributed. `bms-mstp` does not change bacnet-stack's files; it is a separate program built against them.
Example apps in bacnet-stack are MIT.
