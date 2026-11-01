"""Modbus TCP connection to one device (milestone 2 spec 6).

Wraps the pymodbus async client. A read either returns the values or raises:
- DeviceDown: no connection, or no answer within the timeout and retries;
- DeviceFault: the device answered with a Modbus exception.
The BMS reconnects itself, with back-off, instead of pymodbus doing it in the background.
"""

from __future__ import annotations

import logging

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from devine_bms.config.model import ModbusTcpConnection, ObjectType

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


class DeviceDown(Exception):
    pass


class DeviceFault(Exception):
    pass


class ModbusTcpDevice:
    def __init__(self, name: str, connection: ModbusTcpConnection) -> None:
        self.name = name
        self.connection = connection
        self._client = AsyncModbusTcpClient(
            connection.host,
            port=connection.port,
            timeout=connection.timeout_s,
            retries=connection.retries,
            reconnect_delay=0,  # no background reconnects; the poller decides when to retry
        )

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

    async def read(self, object_type: ObjectType, address: int, count: int) -> list[int] | list[bool]:
        if not self._client.connected and not await self.connect():
            raise DeviceDown(f"cannot connect to {self.connection.host}:{self.connection.port}")
        unit = self.connection.unit_id
        try:
            match object_type:
                case ObjectType.HOLDING_REGISTER:
                    result = await self._client.read_holding_registers(address, count=count, device_id=unit)
                case ObjectType.INPUT_REGISTER:
                    result = await self._client.read_input_registers(address, count=count, device_id=unit)
                case ObjectType.COIL:
                    result = await self._client.read_coils(address, count=count, device_id=unit)
                case ObjectType.DISCRETE_INPUT:
                    result = await self._client.read_discrete_inputs(address, count=count, device_id=unit)
        except ModbusException as exc:  # timeouts (ModbusIOException) and lost connections
            self.close()
            raise DeviceDown(f"no answer: {exc}") from exc
        except OSError as exc:
            self.close()
            raise DeviceDown(f"connection error: {exc}") from exc

        if result.isError():
            code = getattr(result, "exception_code", None)
            name = EXCEPTION_NAMES.get(code, "unknown")
            raise DeviceFault(f"Modbus exception {code} ({name})")
        if object_type in (ObjectType.COIL, ObjectType.DISCRETE_INPUT):
            return list(result.bits[:count])
        return list(result.registers)
