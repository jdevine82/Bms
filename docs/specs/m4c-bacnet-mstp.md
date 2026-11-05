# Milestone 4c: BACnet MS/TP

Status: **built and tested on a virtual trunk; not yet bench tested** (5 Nov 2026). Main spec: 4, 5.1, 9, 11, 16 (bench tests 3, 4, 5, 10, 18), 18 (milestone 4), 20 (H2, H3, H4).

## Decisions

| Question | Decision |
|---|---|
| How the backend reaches MS/TP devices | **A service with a local API** (main spec 11): `bms-mstp` on the Proxmox host owns the trunk and answers requests from `bms-backend`. The BACnet router between MS/TP and BACnet/IP stays a separate piece of work (milestone 8). |
| BACnet stack | **bacnet-stack 1.6.1**, pinned by tag and commit hash (released 28 Aug 2026; 1.6.2 and 1.7.0 were one day old and not used). |
| Building | `bms-mstp` is compiled in this project (`mstp/build.sh`); the repository holds only our source, not bacnet-stack's. |

## What is built

- **`mstp/bms-mstp/main.c`:** a C service on bacnet-stack, one process per trunk, an MS/TP master node. Settings come from bacnet-stack's environment variables (port, baud rate, MAC, Max_Master, Max_Info_Frames) and its own options (API address, device instance, request timeout and retries).
  - **Local API:** text requests and JSON answers over TCP. `PING`, `STATS` (frames, invalid frames, lost tokens, CRC errors, poll-for-master), `WHOIS`, and `READ` (one ReadPropertyMultiple with a value or an error per property).
  - **Read-only:** it never writes to a device.
  - **Timeout:** bacnet-stack's MS/TP default is 60 seconds per request, which is far too long for polling; the service defaults to 3 seconds and 1 retry, set per trunk.
- **`mstp/build.sh`:** fetches bacnet-stack 1.6.1, refuses to build if the tag does not point at the expected commit, and builds `bms-mstp` (and, for tests, bacnet-stack's MS/TP test server).
- **`mstp/bms-mstp@.service`:** one systemd service per trunk, settings in `/etc/devine-bms/mstp-<trunk>.env`, restart on failure, its own user in the `dialout` group, a raised priority for MS/TP timing **[Assumed]**, and systemd hardening.
- **Site configuration:** a bus of type `bacnet_mstp` (the trunk: service address, port, baud rate, the server's MAC, Max_Master, Max_Info_Frames, the service's device instance) and devices with `"protocol": "bacnet_mstp"` (trunk, MAC, device instance).
- **Checks** (main spec 5.1 and 9):
  - **MAC 0 and 1 are reserved** for the site server and the Pi HMI; field devices use 2 to 127;
  - MACs are unique on a trunk;
  - a device MAC above the trunk's Max_Master is a warning, because a master there never gets the token;
  - a serial port is never shared, including between a Modbus line and an MS/TP trunk;
  - device instances are unique across BACnet/IP devices, MS/TP devices, the BMS and each trunk's service node.
- **Backend:** one poller per trunk sends one request at a time through the service. Each point reads its present value and status flags, with the same value quality as BACnet/IP (4b): object errors, the fault flag and out-of-service are Bad (fault).
  - Service unreachable or silent: every device on the trunk is down, retried with back-off.
  - One device not answering: only that device is down; the rest of the trunk keeps polling.
  - The whole request rejected or aborted by a device: that block is fault.
- **Trunk health:** `/api/health` shows each trunk's counters (`trunkStats`), refreshed every 30 seconds, for the health page (main spec 5.9).
- **Database:** migration `0004` lets the bus table hold MS/TP trunks.
- **Tests:** a Python stand-in for the service (`simulators/mstp_service.py`) for fast tests, and a test of the **real compiled service against a real bacnet-stack MS/TP device**, on two pseudo-terminals joined by socat: token passing, Who-Is, ReadPropertyMultiple, an unknown object, a missing device, and the trunk counters.
- **Example:** `config/site.example-mstp.json`.

## Not built yet

- **Devices without ReadPropertyMultiple** on MS/TP: the service only sends RPM. A device that rejects it shows its points as fault with the reason. A ReadProperty fallback (as on BACnet/IP) is added if a supported device needs it.
- **COV on MS/TP:** polling only for now. MS/TP COV saves trunk time (H4 supports it); added after bench test 4 shows whether polling keeps up.
- **Writing:** milestone 7.
- **HMI takeover** (the Pi at MAC 1 staying silent until takeover): milestone 11.
- **The BACnet router** (MS/TP to BACnet/IP): milestone 8.

## Bench checks (later, with hardware)

From main spec 16:
1. **Bench test 3:** `bms-mstp` on the Proxmox host as master on a real trunk with several devices, 72 hours: no token loss (`lostToken`), near-zero CRC errors (`badCrc`), and Who-Is finds every device.
2. **Bench test 4:** the full point list of the largest planned trunk at its planned intervals, with no growth in timeouts or retries. If polling cannot keep up, add MS/TP COV.
3. **Bench test 5:** the RS485 adapter keeps its name across reboots (udev rule).
4. **Raised priority:** whether `Nice=-10` is needed, or whether real-time scheduling is, to keep MS/TP reply timing on a busy host.
5. **H2, H3 and H4** read correctly; check whether each supports ReadPropertyMultiple.
