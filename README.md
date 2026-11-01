# Devine BMS

Building management system for HVAC/refrigeration, power metering, solar/battery and lighting across standalone sites.

Status: **planning / pre-build.** Start with [`devine-bms-specification.md`](devine-bms-specification.md); it is the single reference for the project.

| Folder | Contents |
|---|---|
| `docs/decisions/` | One short note per decision |
| `docs/specs/` | One spec per feature |
| `docs/mockups/` | HMI and web app design mockups (open in a browser) |
| `backend/` | FastAPI backend, drivers, alarms, logic runtime |
| `mstp/` | `bacnet-stack` MS/TP and router service |
| `web/` | Site web UI, graphics editor, logic editor |
| `hmi/` | Pi kiosk config and HMI failover code |
| `simulators/` | Fake devices for testing |
| `deploy/` | Proxmox provisioning scripts and service files |
| `tests/` | Automated tests |

Proprietary. All rights reserved.
