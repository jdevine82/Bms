# Milestone 4d: MQTT field devices

Status: **built, not yet bench tested** (5 Nov 2026). Main spec: 4 (aiomqtt, Mosquitto), 5.1, 5.2.1, 11 (bms-mqtt), 18 (milestone 4).

## Decision

| Question | Decision |
|---|---|
| Payload formats | **Plain values and JSON with a field path**, plus an optional **availability topic** per device. This covers Tasmota, Shelly, Zigbee2MQTT and most ESP32 code. Sparkplug B is not supported. |

## What is built

- **Brokers** are buses of type `mqtt_broker`: host, port, username, TLS, client ID and keep-alive. **The password is never in the config file**: `passwordEnv` names an environment variable that holds it (main spec 8 and 12).
- **MQTT devices** (`"protocol": "mqtt"`) name their broker and, optionally, an availability topic with its online and offline texts (for example Tasmota's `tele/<name>/LWT` with `Online` and `Offline`).
- **MQTT point addresses:** one exact `topic`, and an optional `jsonPath` such as `ENERGY.Power`, `temperature` or `channels.0.value`. Bool points read `ON`, `true`, `1`, `online` and similar as true and `OFF`, `false`, `0`, `offline` as false; the lists can be changed per point. Str points match their enum list, ignoring case.
- **Checks:** no wildcards (`+`, `#`) or `$` topics in point addresses; the broker exists; a point's address matches its device's protocol; the same text cannot be both true and false.
- **Reading:** MQTT pushes values, so it is not polled. One connection per broker subscribes (QoS 1) to every point topic and availability topic. Retained values arrive at once, so those points are Good straight away.
- **Quality** (main spec 5.2.1):
  - a payload that does not give a usable value (not a number, missing JSON field, unknown word): that point is Bad (fault) with the reason; other points from the same message are not affected;
  - the device's availability topic says offline: every point of that device is Bad (down) until a new value arrives;
  - the broker is lost: every point on it is Bad (down); it reconnects with back-off;
  - **no message within the stale time:** Stale. For MQTT the speed class means the expected update interval, and the stale time is 3 times it. Devices that publish rarely (Tasmota every 5 minutes by default, battery sensors less often) need a `staleAfterS` to match.
- **Health:** `/api/health` lists each broker connection and its devices; messages count as polls and unusable payloads and offline reports as failures.
- **Bench tool** (read-only): lists every topic and JSON path a broker carries, to help write the configuration:

  ```bash
  uv run python -m devine_bms.drivers.mqtt.tools listen --host 192.168.10.5 --seconds 30
  ```

- **Simulator:** `simulators/mqtt_devices.py` publishes like a Tasmota plug, a Zigbee2MQTT room sensor and a plain retained topic.
- **Example:** `config/site.example-mqtt.json`. Migration `0005` lets the bus table hold brokers.

## Fixed in this milestone

**MS/TP devices were not linked to their trunk in the database** (from 4c). The tests used in-memory storage and missed it; on a site, MS/TP devices would not have been polled. Fixed, with a database test that fails without the fix.

## Not built yet

- **Commands by MQTT** (publishing to a device): milestone 7.
- **Topic discovery** in the web app: milestone 5 (the bench tool covers it for now).
- **Takeover:** MQTT devices are not covered in direct mode (main spec 9).
- **Retained values can be old:** the BMS cannot tell how old a retained value is. A device that died long ago but left a retained value shows Good until its stale time passes. Availability topics catch this for devices that use them.

## Licences

`aiomqtt` is BSD; `paho-mqtt` (which it uses) is EPL-2.0 or BSD-3-Clause, and the BSD option applies. Recorded for main spec section 1.

## Tests (no hardware)

Against a real Mosquitto broker started for each test: values from the simulated devices; a retained value Good at start; a bad payload faulting one point only; a device going offline and back; the broker lost and back; Stale when a device goes quiet; a password from an environment variable (and a wrong one failing); the listen tool. Plus payload parsing, config checks and a database round trip. Repeated 3 times without a failure.

## Bench checks (later, with devices)

1. Real Tasmota, Shelly or Zigbee2MQTT devices on the site's Mosquitto: topics and JSON paths found with the listen tool, values matching each device.
2. Pull a device's power: its availability topic (LWT) goes offline and its points go Bad.
3. Restart Mosquitto: points go down, then come back by themselves.
4. Stale times set to suit each device's publish interval.
