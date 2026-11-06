"""Write one value to one point, on whatever protocol its device speaks (milestone 7A).

- Modbus: a coil (FC05) or holding registers (FC06/FC16), the point's scale and offset
  reversed; Str points write their enum index.
- BACnet/IP and MS/TP: WriteProperty of presentValue at a priority (8 for people, 16 for
  schedules, milestone 7 decision 2); `value=None` releases that priority.
- MQTT: a publish to the point's command topic.

Serial lines and the MS/TP trunk are written through the bus's own poller connection, between
polls, so a port is never opened twice.
"""

from __future__ import annotations

from typing import Any

from devine_bms.config.model import BacnetAddress, Kind, ModbusAddress, MqttAddress, ObjectType
from devine_bms.drivers.errors import DeviceDown, DeviceFault
from devine_bms.drivers.modbus.codec import DecodeError, encode_registers
from devine_bms.runtime_model import RuntimeDevice, RuntimePoint

MANUAL_PRIORITY = 8
SCHEDULE_PRIORITY = 16
PRESENT_VALUE = 85


class WriteError(Exception):
    pass


def _device(runtime, point: RuntimePoint) -> RuntimeDevice:
    device = next((d for d in runtime.config.devices if d.id == point.device_id), None)
    if device is None or not device.enabled:
        raise WriteError("the point's device is not enabled")
    return device


def _poller(runtime, device: RuntimeDevice):
    from devine_bms.setup.testread import _poller_for

    poller = _poller_for(runtime, device)
    if poller is None or not hasattr(poller, "submit"):
        raise WriteError(f"{device.name} has no running connection")
    return poller


def modbus_words(point: RuntimePoint, value: Any) -> list[int] | bool:
    a: ModbusAddress = point.address
    if a.object_type == ObjectType.COIL:
        return bool(value)
    number = point.enum.index(value) if point.kind == Kind.STR else (1.0 if value is True else float(value))
    try:
        return encode_registers(number, a.data_type, a.byte_order, a.word_order, a.scale, a.offset)
    except DecodeError as exc:
        raise WriteError(str(exc)) from exc


def bacnet_value(point: RuntimePoint, value: Any) -> tuple[str, Any]:
    """(tag, value) for the MS/TP service; also used to build bacpypes3 values for BACnet/IP."""
    if value is None:
        return "null", None
    family = point.address.object_type.value
    if family.startswith("binary"):
        return "enum", 1 if value else 0
    if family.startswith("multiState"):
        return "unsigned", point.enum.index(value) + 1 if point.kind == Kind.STR else int(value)
    return "real", float(value)


async def write_point(runtime, point: RuntimePoint, value: Any, priority: int = MANUAL_PRIORITY) -> None:
    device = _device(runtime, point)
    a = point.address
    try:
        if isinstance(a, ModbusAddress):
            if value is None:
                raise WriteError("Modbus has no priorities to release")
            words = modbus_words(point, value)
            unit = device.connection.unit_id
            await _poller(runtime, device).submit(
                lambda driver: driver.client.write(unit, a.object_type, a.address, words), timeout_s=30)
        elif isinstance(a, BacnetAddress) and device.protocol == "bacnet_mstp":
            from devine_bms.drivers.bacnet_mstp.client import OBJECT_TYPE_NUMBERS

            tag, raw = bacnet_value(point, value)
            mac = device.connection.mac
            number = OBJECT_TYPE_NUMBERS[a.object_type.value]
            await _poller(runtime, device).submit(
                lambda driver: driver.client.write(mac, number, a.instance, PRESENT_VALUE, priority, tag, raw),
                timeout_s=30)
        elif isinstance(a, BacnetAddress):
            await _bacnet_ip(runtime, device, point, value, priority)
        elif isinstance(a, MqttAddress):
            await _mqtt(runtime, device, point, value)
        else:
            raise WriteError("this point's address cannot be written")
    except (DeviceDown, DeviceFault) as exc:
        raise WriteError(str(exc)) from exc
    except TimeoutError as exc:
        raise WriteError("the device's connection was busy for too long") from exc


async def _bacnet_ip(runtime, device: RuntimeDevice, point: RuntimePoint, value: Any, priority: int) -> None:
    import asyncio

    from bacpypes3.basetypes import BinaryPV
    from bacpypes3.primitivedata import Null, Real, Unsigned

    from devine_bms.drivers.bacnet.driver import object_id
    from devine_bms.drivers.bacnet.link import device_address

    if runtime.bacnet is None:
        raise WriteError("the BACnet link is not running")
    tag, raw = bacnet_value(point, value)
    bvalue = {"null": lambda: Null(()), "enum": lambda: BinaryPV("active" if raw else "inactive"),
              "unsigned": lambda: Unsigned(raw), "real": lambda: Real(raw)}[tag]()
    conn = device.connection
    try:
        result = await asyncio.wait_for(
            runtime.bacnet.app.write_property(device_address(conn.address), object_id(point.address),
                                              "presentValue", bvalue, priority=priority),
            timeout=conn.timeout_s * (conn.retries + 1))
    except TimeoutError:
        raise DeviceDown(f"no answer from {conn.address}") from None
    except Exception as exc:  # bacpypes3 raises ErrorRejectAbortNack and others for refusals
        raise DeviceFault(f"the device refused the write: {exc}") from exc
    if result is not None and type(result).__name__ in ("ErrorPDU", "RejectPDU", "AbortPDU"):
        raise DeviceFault(f"the device refused the write: {result}")


async def _mqtt(runtime, device: RuntimeDevice, point: RuntimePoint, value: Any) -> None:
    a: MqttAddress = point.address
    if not a.command_topic:
        raise WriteError("the point has no command topic")
    if value is None:
        raise WriteError("MQTT has no priorities to release")
    text = (a.command_true if value else a.command_false) if point.kind == Kind.BOOL else (
        f"{value:g}" if isinstance(value, float) else str(value))
    payload = a.command_template.replace("{value}", text) if a.command_template else text
    link = next((b for b in runtime.buses if getattr(b, "settings", None) is not None
                 and getattr(b.settings, "type", "") == "mqtt_broker" and device.connection.broker == b.name), None)
    if link is None:
        raise WriteError("the MQTT broker connection is not running")
    await link.publish(a.command_topic, payload)
