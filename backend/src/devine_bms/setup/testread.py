"""Test reads (main spec 5.1, step 4; milestone 5A).

Read one point, some points or a device's whole point map, and see each value, the raw
request and answer, errors and timing. Read-only.

A saved device is read through its connection's own poller, between polls, so a serial
port is never opened twice. A device that is not saved yet (the body gives its config)
uses the poller of the bus it names when one runs, and otherwise a short-lived connection.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import aiomqtt

from devine_bms.config.model import ModbusAddress, MqttAddress
from devine_bms.config.validate import ConfigError, parse_config
from devine_bms.drivers.errors import DeviceDown, DeviceFault
from devine_bms.drivers.modbus.client import ModbusClient
from devine_bms.poller.blocks import make_blocks
from devine_bms.poller.device_poller import BusPoller, PointError
from devine_bms.poller.quality import point_value as modbus_point_value
from devine_bms.runtime_model import RuntimeDevice, RuntimePoint, from_site_config
from devine_bms.setup.service import SetupError

FUNCTION_CODES = {"coil": 1, "discreteInput": 2, "holdingRegister": 3, "inputRegister": 4}


def _value_json(point: RuntimePoint, outcome: Any) -> dict:
    if isinstance(outcome, PointError):
        return {"point": point.name, "status": outcome.status.value, "value": None, "error": outcome.reason}
    return {"point": point.name, "status": "ok", "value": outcome, "unit": point.unit}


async def _modbus_job(driver, device: RuntimeDevice, points: list[RuntimePoint]) -> dict:
    client: ModbusClient = driver.client
    results, requests = [], []
    for block in make_blocks(points, device.block_size):
        description = (f"unit {device.unit_id}, function {FUNCTION_CODES[block.object_type.value]:02d}, "
                       f"wire address {block.start}, count {block.count}")
        client.start_capture()
        started = time.monotonic()
        error = None
        try:
            raw = await client.read(device.unit_id, block.object_type, block.start, block.count)
        except (DeviceDown, DeviceFault) as exc:
            raw, error = None, str(exc)
        elapsed = round((time.monotonic() - started) * 1000, 1)
        packets = client.stop_capture()
        requests.append({"request": description, "ms": elapsed, "error": error,
                         "frames": [{"direction": d, "hex": data.hex(" ")} for d, data in packets],
                         "registers": raw})
        for point in block.points:
            if raw is None:
                results.append({"point": point.name, "status": "down" if "answer" in (error or "") else "fault",
                                "value": None, "error": error})
                continue
            try:
                results.append(_value_json(point, modbus_point_value(point, block, raw)))
            except ValueError as exc:
                results.append({"point": point.name, "status": "fault", "value": None, "error": str(exc)})
    return {"results": results, "requests": requests}


async def _bacnet_job(driver, device: RuntimeDevice, points: list[RuntimePoint]) -> dict:
    from devine_bms.drivers.bacnet.driver import make_bacnet_blocks
    from devine_bms.poller.device_poller import DeviceState

    state = DeviceState(device, points, None, make_bacnet_blocks(points, device.block_size))
    saved_cov, driver_cov = None, getattr(driver, "cov_active", None)
    if driver_cov is not None:  # a test read always asks the device, even for COV points
        saved_cov, driver.cov_active = driver_cov, set()
    results, requests = [], []
    try:
        for block in state.blocks:
            started = time.monotonic()
            error = None
            try:
                outcomes = await driver.read_block(state, block)
            except (DeviceDown, DeviceFault) as exc:
                outcomes, error = [], str(exc)
            elapsed = round((time.monotonic() - started) * 1000, 1)
            exchange = getattr(getattr(driver, "client", None), "last_exchange", None)
            objects = [f"{p.address.object_type.value} {p.address.instance}" for p in block.points]
            requests.append({"request": f"ReadPropertyMultiple of present-value and status-flags: {objects}",
                             "ms": elapsed, "error": error,
                             "frames": ([{"direction": "sent", "text": exchange[0]},
                                         {"direction": "received", "text": exchange[1]}] if exchange else [])})
            if error:
                results += [{"point": p.name, "status": "down" if "answer" in error else "fault", "value": None,
                             "error": error} for p in block.points]
            else:
                results += [_value_json(p, outcome) for p, outcome in outcomes]
    finally:
        if saved_cov is not None:
            driver.cov_active = saved_cov
    return {"results": results, "requests": requests}


async def _mqtt_read(broker, points: list[RuntimePoint], wait_s: float) -> dict:
    """MQTT is not polled: listen on the points' topics for a while (retained values come at once)."""
    import os
    import uuid

    from devine_bms.drivers.mqtt.payload import PayloadError, point_value

    topics = sorted({p.address.topic for p in points})
    seen: dict[str, bytes] = {}
    password = os.environ.get(broker.password_env) if broker.password_env else None
    async with aiomqtt.Client(broker.host, broker.port, username=broker.username, password=password,
                              identifier=f"devine-bms-test-{uuid.uuid4().hex[:8]}") as client:
        for topic in topics:
            await client.subscribe(topic)
        try:
            async with asyncio.timeout(wait_s):
                async for message in client.messages:
                    seen[str(message.topic)] = bytes(message.payload or b"")
                    if set(seen) >= set(topics):
                        break
        except TimeoutError:
            pass
    results = []
    for p in points:
        payload = seen.get(p.address.topic)
        if payload is None:
            results.append({"point": p.name, "status": "stale", "value": None,
                            "error": f"no message on {p.address.topic} within {wait_s:g} s"})
            continue
        try:
            results.append({"point": p.name, "status": "ok", "value": point_value(p, payload), "unit": p.unit})
        except PayloadError as exc:
            results.append({"point": p.name, "status": "fault", "value": None, "error": str(exc)})
    return {"results": results,
            "requests": [{"request": f"listen on {t}", "frames": [
                {"direction": "received", "text": seen[t].decode("utf-8", "replace")}] if t in seen else [],
                "error": None if t in seen else "no message"} for t in topics]}


def _poller_for(runtime, device: RuntimeDevice) -> BusPoller | None:
    for poller in runtime.buses:
        if any(state.device.name == device.name for state in poller.devices):
            return poller
    bus_name = (getattr(device.connection, "bus", None) or getattr(device.connection, "trunk", None)
                or getattr(device.connection, "broker", None))
    if bus_name:
        return next((p for p in runtime.buses if p.name == bus_name and hasattr(p, "submit")), None)
    return None


async def test_read(runtime, setup, body: dict) -> dict:
    """body: {"device": "<saved device>", "points": [names]?} or
    {"config": {"device": {...}, "points": [{...}], "bus": {...}?}} for a device not saved yet."""
    wait_s = float(body.get("waitS", 10))
    if "config" in body:
        device, points, rc_bus = await _unsaved(setup, body["config"])
    else:
        name = body.get("device")
        device = next((d for d in runtime.config.devices if d.name == name), None)
        if device is None:
            raise SetupError(f"no saved device '{name}'", status=404)
        points = runtime.config.points_for(device.id)
        rc_bus = next((b for b in runtime.config.buses if b.id == device.bus_id), None)
    wanted = body.get("points")
    if wanted:
        points = [p for p in points if p.name in set(wanted)]
    if not points:
        raise SetupError("no points to read")

    started = time.monotonic()
    family = type(points[0].address)
    if family is MqttAddress:
        if rc_bus is None:
            raise SetupError("the MQTT device's broker is not known")
        out = await _mqtt_read(rc_bus.settings, points, wait_s)
    else:
        job = _modbus_job if family is ModbusAddress else _bacnet_job
        # A changed but unsaved device with its own connection (TCP or BACnet/IP) may now point
        # somewhere else, so it never borrows the saved device's connection.
        own_connection = "config" in body and device.protocol in ("modbus_tcp", "bacnet_ip")
        poller = None if own_connection else _poller_for(runtime, device)
        if poller is not None:
            out = await poller.submit(lambda driver: job(driver, device, points), timeout_s=120)
        else:
            out = await _short_lived(runtime, device, points, rc_bus, job)
    return {"ok": True, "device": device.name, "ms": round((time.monotonic() - started) * 1000, 1), **out}


async def _short_lived(runtime, device, points, rc_bus, job) -> dict:
    """A connection just for this test read, when no poller owns one."""
    if device.protocol == "modbus_tcp" or (rc_bus and rc_bus.settings.type.startswith("modbus")):
        client = (ModbusClient.for_tcp_device(device.name, device.connection) if device.protocol == "modbus_tcp"
                  else ModbusClient.for_bus(rc_bus.settings))
        from devine_bms.poller.device_poller import ModbusDriver
        try:
            return await job(ModbusDriver(client), device, points)
        finally:
            client.close()
    if device.protocol == "bacnet_mstp" and rc_bus is not None:
        from devine_bms.drivers.bacnet_mstp.client import MstpServiceClient
        from devine_bms.drivers.bacnet_mstp.driver import MstpDriver
        client = MstpServiceClient(rc_bus.settings.service, rc_bus.settings.timeout_s)
        try:
            return await job(MstpDriver(rc_bus.name, client, rc_bus.settings.port), device, points)
        finally:
            client.close()
    if device.protocol == "bacnet_ip":
        if runtime.bacnet is None:
            raise SetupError("the BACnet link is not running; save the site's BACnet settings first")
        from devine_bms.drivers.bacnet.driver import BacnetDriver
        return await job(BacnetDriver(runtime.bacnet, device), device, points)
    raise SetupError(f"test reads for {device.protocol} need the device's bus to exist")


async def _unsaved(setup, fragment: dict):
    """Check a device that is not saved yet against the saved setup, and build runtime objects for it."""
    data = await setup.export()
    device_cfg = fragment.get("device") or {}
    if any(d["name"] == device_cfg.get("name") for d in data.get("devices", [])):
        data["devices"] = [d for d in data["devices"] if d["name"] != device_cfg.get("name")]
        data["points"] = [p for p in data.get("points", []) if p.get("device") != device_cfg.get("name")]
    if fragment.get("bus"):
        data["buses"] = [b for b in data.get("buses", []) if b["name"] != fragment["bus"].get("name")]
        data["buses"].append(fragment["bus"])
    data.setdefault("devices", []).append(device_cfg)
    # A test read needs only the addresses. The points may belong to equipment or rooms that
    # are not saved yet (the commissioning wizard's draft), so a stand-in equipment holds them.
    owner = "test_read_points"
    data.setdefault("equips", []).append({"name": owner, "dis": "Test read"})
    points = [{k: v for k, v in p.items() if k not in ("equip", "space")} | {"equip": owner}
              for p in fragment.get("points", []) if isinstance(p, dict)]
    data.setdefault("points", []).extend(points)
    try:
        config, _ = parse_config(data)
    except ConfigError as exc:
        raise SetupError("the device to test is not valid", issues=exc.issues) from exc
    rc = from_site_config(config)
    device = next(d for d in rc.devices if d.name == device_cfg.get("name"))
    bus = next((b for b in rc.buses if b.id == device.bus_id), None)
    return device, list(rc.points_for(device.id)), bus

