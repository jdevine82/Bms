# Milestone 8: BACnet router between MS/TP and BACnet/IP

Status: **planned** (6 Nov 2026). Main spec: 3 (bms-mstp), 5.1 (BACnet router v1), 16 (bench test 6), 18 (milestone 8), 19 open items 6 and 7.

Other BACnet/IP tools on the device network (a commissioning laptop, another vendor's front end) should see the devices on the MS/TP trunks. A BACnet router does that: it joins an MS/TP network and a BACnet/IP network and passes messages between them by network number.

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | Where the router runs | **Inside bms-mstp.** Each trunk's service also routes, using bacnet-stack's router code (`apps/router-mstp`, MIT licence) merged in; the BMS keeps reading MS/TP devices through the tested local API (milestone 4c). Each routing trunk's BACnet/IP side has **its own IP address on the device-network port** (an address the installer adds to the host), so every router and the BMS itself can use UDP 47808. |
| 2 | Network numbers | **Set per site, with defaults.** The BACnet/IP network number is a site setting (`bacnet.networkNumber`, default 1); each MS/TP trunk has `networkNumber` in its bus settings (suggested 101, 102, …). The setup checks they are unique; the router warns when another router on the network announces the same number. |

## What is built

- **Config:** `networkNumber` on the site's BACnet settings and on MS/TP trunks; `router: {enabled, address}` on a trunk, where `address` is the trunk's own IP address on the device network (port 47808 unless given). Checks: network numbers 1 to 65534 and unique; router addresses unique and not the BMS's own BACnet/IP address; a router needs the site's BACnet settings.
- **bms-mstp 0.3.0:** with `--route ADDRESS[:PORT] --ip-net N --mstp-net M` it opens a BACnet/IP port on that address and routes between it and the trunk: it forwards NPDUs both ways with the right SNET/SADR and DNET/DADR, answers Who-Is-Router-To-Network, announces I-Am-Router-To-Network at start, and logs a warning when another router announces its MS/TP network number. Without `--route` it behaves exactly as 0.2.0. The local API gains `ROUTER <id>` (routing on or off, both network numbers, packets routed each way, rejects).
- **The BMS** passes the options from the trunk settings (systemd unit and `mstp-trunk.env`), and shows the router counters with the trunk's health.
- **Bench test 6** (main spec 16): a laptop BACnet tool on the device network sees and reads MS/TP devices through the router; stable for 24 hours.

## Not in this milestone

- BBMD and foreign-device registration (BACnet/IP across subnets).
- Routing between two MS/TP trunks directly (each trunk routes to BACnet/IP; a tool reaches every trunk through the IP network).
