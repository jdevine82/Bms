# The BACnet router lives in bms-mstp

Date: 6 Nov 2026. Status: decided.

Each MS/TP trunk's bms-mstp service also routes between its trunk and BACnet/IP, using bacnet-stack's router code, so the serial port has one owner and the BMS keeps its local API. Each routing trunk has its own IP address on the device-network port, so all routers and the BMS can use UDP 47808. Network numbers are set per site (BACnet/IP default 1, trunks suggested 101 upwards), checked for uniqueness, and a clash with another router's announcement is logged.

Spec: main spec 5.1, 16, 18; docs/specs/m8-bacnet-router.md
