# BACnet MS/TP service

Date: 5 Nov 2026. Status: decided.

MS/TP trunks are run by bms-mstp, a C service on bacnet-stack 1.6.1 (pinned by tag and commit) on the Proxmox host, one process per trunk, as MAC 0. bms-backend reads devices through its local API (text requests, JSON answers over TCP). The BACnet router to BACnet/IP is separate (milestone 8). MAC 0 and 1 are reserved on every trunk. The service only reads in this milestone.

bacnet-stack's library is GPL-2.0-or-later with the GCC exception, which allows linking it into proprietary code; its own files are not changed.

Spec: main spec 4, 9, 11; docs/specs/m4c-bacnet-mstp.md
