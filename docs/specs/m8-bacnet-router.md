# Milestone 8: BACnet router between MS/TP and BACnet/IP

Status: **built, bench test 6 to do** (6 Nov 2026). Main spec: 3 (bms-mstp), 5.1 (BACnet router v1), 16 (bench test 6), 18 (milestone 8), 19 open items 6 and 7.

Other BACnet/IP tools on the device network (a commissioning laptop, another vendor's front end) should see the devices on the MS/TP trunks. A BACnet router does that: it joins an MS/TP network and a BACnet/IP network and passes messages between them by network number.

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | Where the router runs | **Inside bms-mstp.** Each trunk's service also routes, using bacnet-stack's router code (`apps/router-mstp`, MIT licence) merged in; the BMS keeps reading MS/TP devices through the tested local API (milestone 4c). Each routing trunk's BACnet/IP side has **its own IP address on the device-network port** (an address the installer adds to the host), so every router and the BMS itself can use UDP 47808. |
| 2 | Network numbers | **Set per site, with defaults.** The BACnet/IP network number is a site setting (`bacnet.networkNumber`, default 1); each MS/TP trunk has `networkNumber` in its bus settings (suggested 101, 102, …). The setup checks they are unique; the router warns when another router on the network announces the same number. |

## What is built

- **Config:** `networkNumber` on the site's BACnet settings and on MS/TP trunks; `router: {enabled, address}` on a trunk, where `address` is the trunk's own IP address on the device network (port 47808 unless given). Checks: network numbers 1 to 65534 and unique; router addresses unique and not the BMS's own BACnet/IP address; a router needs the site's BACnet settings.
- **bms-mstp 0.3.0:** with `--route IFACE[:PORT] --ip-net N --mstp-net M` it opens a BACnet/IP port on that interface's address (the trunk's labelled address, such as `eth1:mstp1`) and routes between it and the trunk: it forwards NPDUs both ways with the right SNET/SADR and DNET/DADR, answers Who-Is-Router-To-Network, announces I-Am-Router-To-Network at start, and logs a warning when another router announces its MS/TP network number. Without `--route` it behaves exactly as 0.2.0. The local API gains `ROUTER <id>` (routing on or off, both network numbers, packets routed each way, rejects).
- **The BMS** passes the options from the trunk settings (systemd unit and `mstp-trunk.env`), and shows the router counters with the trunk's health.
- **Bench test 6** (main spec 16): a laptop BACnet tool on the device network sees and reads MS/TP devices through the router; stable for 24 hours.

## Not in this milestone

- BBMD and foreign-device registration (BACnet/IP across subnets).
- Routing between two MS/TP trunks directly (each trunk routes to BACnet/IP; a tool reaches every trunk through the IP network).

## How it was built

- `mstp/bms-mstp/router.c` is bacnet-stack 1.6.1's `apps/router-mstp` routing code (MIT), adapted: bms-mstp supplies the datalink functions itself, so its own requests go to the trunk (or, for a routed destination, through the router), a route to one of its own networks is refused and counted as a clash, and traffic is counted.
- The library is built with both datalinks (`BACDL=bip-mstp` with `BACDL_DEFINE=-DBACDL_NONE=1`, so bacnet-stack does not pick one); `build.sh` does this, and rebuilds it per tool for the test tools.
- Without `--route` the service behaves as 0.2.0: the local API, reads and writes are unchanged (tested with and without routing).
- The systemd unit takes `BMS_MSTP_ROUTE_ARGS` from the trunk's env file; `mstp/README.md` has the steps, including the labelled address.

## Tests

- Backend: config checks (unique numbers, a routing trunk needs its number and its own address, routing needs the site's BACnet settings), the counters in the trunk's health through the stand-in, and **the real compiled service**: bacnet-stack's MS/TP server on a virtual serial line, bms-mstp routing it, and bacnet-stack's BACnet/IP read tool reading the device's name and a value through network 101; a fake router announcing network 101 is counted as a clash. Needs the binaries from `build.sh --with-test-tools` (`BMS_MSTP_BIN`, `BMS_BACSERV_BIN`, `BMS_BACRP_BIN`) and socat.
- Bench test 6: a laptop BACnet tool, real MS/TP controllers, 24 hours.
