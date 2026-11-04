"""Modbus connections (milestone 2 spec 6, milestone 4a).

One ModbusClient is one connection the BMS polls through:
- a device with its own Modbus TCP connection (`modbus_tcp`);
- an RS485 line on a serial port (`modbus_rtu`);
- a serial-to-Ethernet gateway passing raw RTU frames (`modbus_rtu_over_tcp`);
- a serial-to-Ethernet gateway with Modbus TCP framing (`modbus_tcp_gateway`).

A read returns the values or raises:
- BusDown: the connection itself is gone (port missing, TCP refused or lost). Every
  device on it is down.
- DeviceDown: the connection is fine but this device did not answer in time, or a
  gateway reported its target device unreachable (exceptions 10 and 11).
- DeviceFault: the device answered with a Modbus exception.
The BMS reconnects itself, with back-off, instead of pymodbus doing it in the background.
"""

from __future__ import annotations

import logging

from pymodbus import FramerType
from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient
from pymodbus.exceptions import ConnectionException, ModbusException, ModbusIOException

from devine_bms.config.model import (
    ModbusRtuBus,
    ModbusRtuOverTcpBus,
    ModbusTcpConnection,
    ModbusTcpGatewayBus,
    ObjectType,
)

log = logging.getLogger(__name__)

EXCEPTION_NAMES = {
    1: "illegal function",
    2: "illegal data address",
    3: "illegal data value",
    4: "server device failure",
    5: "acknowledge",
    6: "server device busy",
    10: "gateway path unavailable",
    11: "gateway target device failed to respond",
}
GATEWAY_TARGET_DOWN = {10, 11}


class DeviceDown(Exception):
    pass


class BusDown(DeviceDown):
    pass


class DeviceFault(Exception):
    pass


class ModbusClient:
    def __init__(self, name: str, client: AsyncModbusTcpClient | AsyncModbusSerialClient, where: str,
                 shared: bool) -> None:
        self.name = name
        self.where = where
        self.shared = shared  # several devices on one connection (a bus or gateway)
        self._client = client

    @classmethod
    def for_tcp_device(cls, name: str, conn: ModbusTcpConnection) -> ModbusClient:
        client = AsyncModbusTcpClient(
            conn.host, port=conn.port, timeout=conn.timeout_s, retries=conn.retries, reconnect_delay=0
        )
        return cls(name, client, f"{conn.host}:{conn.port}", shared=False)

    @classmethod
    def for_bus(cls, bus: ModbusRtuBus | ModbusRtuOverTcpBus | ModbusTcpGatewayBus) -> ModbusClient:
        common = {"timeout": bus.timeout_s, "retries": bus.retries, "reconnect_delay": 0}
        if isinstance(bus, ModbusRtuBus):
            client = AsyncModbusSerialClient(
                bus.port, baudrate=bus.baudrate, parity=bus.parity, bytesize=bus.data_bits,
                stopbits=bus.stop_bits, **common,
            )
            where = f"{bus.port} {bus.baudrate} {bus.data_bits}{bus.parity}{bus.stop_bits}"
        elif isinstance(bus, ModbusRtuOverTcpBus):
            client = AsyncModbusTcpClient(bus.host, port=bus.port, framer=FramerType.RTU, **common)
            where = f"{bus.host}:{bus.port} (RTU over TCP)"
        else:
            client = AsyncModbusTcpClient(bus.host, port=bus.port, **common)
            where = f"{bus.host}:{bus.port} (TCP gateway)"
        return cls(bus.name, client, where, shared=True)

    @property
    def connected(self) -> bool:
        return self._client.connected

    async def connect(self) -> bool:
        if self._client.connected:
            return True
        try:
            return bool(await self._client.connect())
        except (OSError, ModbusException) as exc:
            log.debug("connect to %s failed: %s", self.name, exc)
            return False

    def close(self) -> None:
        self._client.close()

    async def read(self, unit_id: int, object_type: ObjectType, address: int, count: int) -> list[int] | list[bool]:
        if not self._client.connected and not await self.connect():
            raise BusDown(f"cannot connect to {self.where}")
        try:
            match object_type:
                case ObjectType.HOLDING_REGISTER:
                    result = await self._client.read_holding_registers(address, count=count, device_id=unit_id)
                case ObjectType.INPUT_REGISTER:
                    result = await self._client.read_input_registers(address, count=count, device_id=unit_id)
                case ObjectType.COIL:
                    result = await self._client.read_coils(address, count=count, device_id=unit_id)
                case ObjectType.DISCRETE_INPUT:
                    result = await self._client.read_discrete_inputs(address, count=count, device_id=unit_id)
        except ConnectionException as exc:
            self.close()
            raise BusDown(f"connection lost to {self.where}: {exc}") from exc
        except ModbusIOException as exc:
            if not self.shared:
                self.close()  # a single device's own connection: start clean next time
            raise DeviceDown(f"no answer: {exc}") from exc
        except ModbusException as exc:
            self.close()
            raise BusDown(f"Modbus error on {self.where}: {exc}") from exc
        except OSError as exc:
            self.close()
            raise BusDown(f"connection error on {self.where}: {exc}") from exc

        if result.isError():
            code = getattr(result, "exception_code", None)
            name = EXCEPTION_NAMES.get(code, "unknown")
            if code in GATEWAY_TARGET_DOWN:
                raise DeviceDown(f"gateway reports Modbus exception {code} ({name})")
            raise DeviceFault(f"Modbus exception {code} ({name})")
        if object_type in (ObjectType.COIL, ObjectType.DISCRETE_INPUT):
            return list(result.bits[:count])
        return list(result.registers)
