# Milestone 4b: BACnet/IP

Status: **built, not yet bench tested** (4 Nov 2026). Main spec: 4 (bacpypes3), 5.1, 5.1.4, 5.2.1, 16 (bench tests 2 and 18), 18 (milestone 4), 20 (H3 to H6).

## What is built

- **The BMS's own BACnet identity:** a site-level `bacnet` block in the config gives the BMS's local address with its network size (for broadcasts), UDP port (default 47808), device instance and device name. It is stored in the new `site.settings` column (migration `0003`).
- **BACnet/IP devices:** `"protocol": "bacnet_ip"` with the device's address (and port when not 47808), its device instance, timeout, retries, properties per request, and an optional `cov` switch.
- **BACnet points:** an address of object type (analog, binary and multi-state input, output and value) and instance; the property is the present value.
  - Analog objects are Number points.
  - Binary objects are Bool points.
  - Multi-state objects are Str points with an `enum` list (state 1 is the first entry), or Number points (the state number).
  - Input objects cannot be `sp` or `cmd` points.
- **Checks:** BACnet devices need the site's `bacnet` settings; device instances are unique (including the BMS's own); a point's address must match its device's protocol.
- **Reading:** each point reads its present value and status flags with ReadPropertyMultiple, grouped by speed class. If a device rejects ReadPropertyMultiple, the BMS falls back to ReadProperty for that device by itself.
- **Quality** (main spec 5.2.1):
  - no answer within the timeout and retries: the device is down;
  - an error for one object (for example `unknown-object`): that point is Bad (fault), the rest are fine;
  - status flag **fault**: Bad (fault);
  - status flag **out-of-service**: Bad (fault), because the value is not live and can be set by hand on the device (main spec 20, H4). **[Assumed]**
- **COV** (main spec 5.1.4): with `cov: true`, Fast and Normal points are subscribed to change-of-value. A notification updates the value at once. The point is still polled at the **Slow** interval as a back-up, and its stale time is 3 times the Slow interval while the subscription works, because COV only reports changes. If a subscription fails (the device or object does not support COV), the point goes back to normal polling by itself, and `/api/health` lists it under `cov.failed`.
- **Bench tools** (read-only):

  ```bash
  uv run python -m devine_bms.drivers.bacnet.tools whois --local 192.168.10.5/24
  ```

  ```bash
  uv run python -m devine_bms.drivers.bacnet.tools read --local 192.168.10.5/24 --device 192.168.10.20 --object analog-input,1
  ```

- **Simulator:** `simulators/bacnet_device.py`, a bacpypes3 device with a room temperature, setpoint, fan status, fan command and mode. Tests can set fault (through reliability, as a real device does), out-of-service, refuse ReadPropertyMultiple, or make the device disappear.
- **Example:** `config/site.example-bacnet.json`, the simulated controller with COV on.
- The polling code is now shared by every protocol: a driver reads a block of points, and the schedule, back-off and quality are the same for Modbus and BACnet.

## Not built yet

- **Finding a device's address by its instance** (Who-Is), for devices whose IP address changes. Addresses are fixed in the config for now; Discover is milestone 5.
- **Writing** (with BACnet priorities and relinquish): milestone 7.
- **Segmented responses and very large devices** (H5 and H6 with hundreds of objects): the properties-per-request setting keeps requests small; checked on the bench.
- **BACnet routing and MS/TP:** milestones 4c and 8.
- **Units:** the BMS uses the unit from the config, not the device's `units` property.

## Licences

`bacpypes3` is under a **BSD licence**, which allows commercial use. Recorded for the licence check in main spec section 1.

## Tests (no hardware)

Against the simulated device over loopback: every object type and its conversion; an unknown object is a per-point fault; fault and out-of-service flags; the device disappearing and coming back; the ReadProperty fallback; a COV notification arriving long before the next poll; a COV failure falling back to polling; the bench tools; config checks; and the BACnet settings and addresses surviving the database. Repeated 4 times without a failure.

## Bench checks (later, with hardware)

From main spec 16:
1. **Bench test 2:** Who-Is finds the bench controllers; reads match what each controller shows.
2. **COV on real devices:** which objects support it (H4 supports SubscribeCOV on AI 1 to 8; H6 needs COV switched on in the Setting Tool), and that a missed notification is caught by the Slow back-up poll.
3. **Out-of-service and fault flags** on H3 and H4.
4. **H5 and H6 at size:** hundreds of objects; find the safe properties per request and whether segmentation is needed.
