# Milestone 4a: Modbus RTU and serial gateways

Status: **built, not yet bench tested** (4 Nov 2026). Main spec: 5.1, 5.1.4, 11, 13, 16 (bench tests 1, 5, 18), 18 (milestone 4), 20 (H1).

Milestone 4 is split into four parts, in the main spec's order: **4a Modbus RTU** (this page), 4b BACnet/IP, 4c BACnet MS/TP, 4d MQTT. Bench testing with real hardware comes later.

## What is built

- **Buses** in the site configuration (`buses`), one per RS485 line or gateway:
  - `modbus_rtu`: an RS485 line on a serial port (USB RS485 adapter), with baud rate, parity, data bits and stop bits;
  - `modbus_rtu_over_tcp`: a serial-to-Ethernet gateway that passes raw RTU frames;
  - `modbus_tcp_gateway`: a gateway with Modbus TCP framing, addressing devices by unit ID.
  Each bus has a timeout and retries. Devices on a bus use `"protocol": "modbus"` and give the bus and their unit ID (1 to 247).
- **Checks:** a serial port is never used by two buses (5.1); two devices on one bus cannot share a unit ID (5.1: many units ship as ID 1); a device must name a bus that exists.
- **Polling:** one poller per bus sends one request at a time, as a serial line needs. Speed classes and block reads as in milestone 2. A direct Modbus TCP device is a connection of its own, as before.
- **Failures on a shared bus:**
  - the bus itself is down (port missing, gateway unreachable): every device on it is down, and the bus is retried with back-off;
  - one device does not answer: only that device is down, the other devices keep being polled, and that device is retried with back-off;
  - a gateway reports exception 10 or 11 (target device not answering): that device is down, not fault.
- **Database:** migration `0002` adds the `bus` table and links devices to their bus.
- **Health API:** `/api/health` lists each connection, where it goes, and its devices.
- **Device templates** (`devine-bms-device-template/0`): a template describes a device type once (point map, default settings, notes). Built-in templates live in `backend/src/devine_bms/templates/builtin/`. The command line lists, shows and expands them into config points:

  ```bash
  uv run python -m devine_bms.templates.device show cwt-mb328b
  ```

  ```bash
  uv run python -m devine_bms.templates.device expand cwt-mb328b --device io1 --equip lights1 --name-prefix plant_lights1_ --only di0,do0
  ```

  The web app's template screens come in milestone 5.
- **H1 template** (`cwt-mb328b`): DI0 to DI7 as discrete inputs 10001 to 10008 (FC02), DO0 to DO17 as coils 00001 to 00018 (FC01). It stores the 0-based wire address and the manual's 1-based address, the default settings (ID 1, 9600 8N1, 192.168.1.1:502), and the watch-outs from main spec 20. **Status: datasheet only.**
- **Simulator:** RTU framing with CRC, several devices on one bus, an H1-style I/O module, per-device fault modes, and a gateway mode that answers exception 11 for a silent device.
- **Stable adapter names:** `deploy/udev/99-devine-bms-rs485.rules` explains naming each USB RS485 adapter by serial number (bench test 5).
- **Example:** `config/site.example-rtu.json`, a meter and an H1 module on one RS485 line.

## Not built yet

- Writing to devices (coils, registers): milestone 7, with write limits and the audit log.
- The device and template screens in the web app, Discover and scanning: milestone 5.
- Flushing a serial line after a timeout. A late answer from one device could, in theory, be read as the answer to the next request. pymodbus checks the unit ID and function code, which should catch it; this is checked on the bench at load (bench test 18).

## Tests (no hardware)

- RTU over TCP gateway with two devices; a serial line through a `socket://` port; a serial line through a **real pseudo-terminal** made by `socat`.
- One silent device does not stop the others, and recovers by itself; the whole bus down marks every device down and recovers by itself.
- A TCP gateway's exception 11 is Bad (down), not fault.
- Config checks: duplicate unit ID, shared serial port, missing bus, unit ID 0.
- The H1 template matches the manual, expands into valid config points, and reads all 26 points from the simulated module (2 requests per cycle).

## Bench checks (later, with hardware)

From main spec 16:
1. **Bench test 1:** poll a real meter or the H1 over Modbus RTU and TCP for 24 hours with no missed polls beyond the configured retries.
2. **Bench test 5:** with the udev rule, each adapter keeps its name after reboot and replug.
3. **Bench test 18 (Modbus part):** poll the full point list on the longest planned RS485 bus; Fast points meet their interval; the Slow class delays first when the bus is full.
4. **H1 open questions** (main spec 20): do coil reads match the relays; what do the relays do on loss of communications and at power-up; how are the ID, baud rate and IP address set.
