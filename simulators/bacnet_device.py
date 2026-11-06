"""Simulated BACnet/IP device for development and tests (milestone 4b).

A bacpypes3 device with:

    analog-input 1       Room temperature (°C), drifting slowly
    analog-value 1       Setpoint (°C), 21.0, commandable (relinquish default 21.0)
    binary-input 1       Fan status, follows binary-output 1
    binary-output 1      Fan command, starts active
    multi-state-value 1  Mode: 1 Cool, 2 Heat, 3 Fan; starts at 2 (Heat)

Faults the tests use: `set_fault(obj)` sets the fault status flag, `set_out_of_service(obj)`
sets out-of-service, `no_rpm=True` refuses ReadPropertyMultiple, and `stop()` makes the
device disappear.

Run:  python simulators/bacnet_device.py --address 127.0.0.1/8 --port 47809 --instance 1001
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import time

from bacpypes3.errors import UnrecognizedService
from bacpypes3.ipv4.app import NormalApplication
from bacpypes3.local.analog import AnalogInputObject, AnalogValueObjectCmd
from bacpypes3.local.binary import BinaryInputObject, BinaryOutputObject
from bacpypes3.local.device import DeviceObject
from bacpypes3.local.multistate import MultiStateValueObject
from bacpypes3.pdu import IPv4Address

log = logging.getLogger("bacnet_sim")
OK_FLAGS = [0, 0, 0, 0]


class NoRpmApplication(NormalApplication):
    """A device that does not support ReadPropertyMultiple, like some older controllers."""

    async def do_ReadPropertyMultipleRequest(self, apdu) -> None:  # noqa: N802 (bacpypes3 name)
        raise UnrecognizedService()


class BacnetSimulator:
    def __init__(self, address: str = "127.0.0.1/8", port: int = 47809, instance: int = 1001,
                 update_interval_s: float = 1.0, no_rpm: bool = False) -> None:
        self.address = address
        self.port = port
        self.instance = instance
        self.update_interval_s = update_interval_s
        self.no_rpm = no_rpm
        self.app: NormalApplication | None = None
        self._task: asyncio.Task | None = None
        self.room_temp = AnalogInputObject(
            objectIdentifier=("analogInput", 1), objectName="Room temperature", presentValue=21.5,
            units="degreesCelsius", statusFlags=OK_FLAGS, eventState="normal", outOfService=False,
            covIncrement=0.1,
        )
        # Commandable (priority array and relinquish default), like a real controller's setpoint.
        self.setpoint = AnalogValueObjectCmd(
            objectIdentifier=("analogValue", 1), objectName="Setpoint", presentValue=21.0, relinquishDefault=21.0,
            units="degreesCelsius", statusFlags=OK_FLAGS, eventState="normal", outOfService=False,
            covIncrement=0.1,
        )
        self.fan_status = BinaryInputObject(
            objectIdentifier=("binaryInput", 1), objectName="Fan status", presentValue="active",
            statusFlags=OK_FLAGS, eventState="normal", outOfService=False, polarity="normal",
        )
        self.fan_command = BinaryOutputObject(
            objectIdentifier=("binaryOutput", 1), objectName="Fan command", presentValue="active",
            statusFlags=OK_FLAGS, eventState="normal", outOfService=False, polarity="normal",
            relinquishDefault="inactive",
        )
        self.mode = MultiStateValueObject(
            objectIdentifier=("multiStateValue", 1), objectName="Mode", presentValue=2, numberOfStates=3,
            stateText=["Cool", "Heat", "Fan"], statusFlags=OK_FLAGS, eventState="normal", outOfService=False,
        )
        self.objects = [self.room_temp, self.setpoint, self.fan_status, self.fan_command, self.mode]

    @property
    def endpoint(self) -> str:
        """The address the BMS reads, for example 127.0.0.1:47809."""
        return f"{self.address.split('/')[0]}:{self.port}"

    async def start(self) -> None:
        device = DeviceObject(
            objectIdentifier=("device", self.instance), objectName=f"Simulated controller {self.instance}",
            vendorIdentifier=999, vendorName="Devine BMS simulator", modelName="SIM-1",
            maxApduLengthAccepted=1024, segmentationSupported="segmentedBoth",
        )
        app_class = NoRpmApplication if self.no_rpm else NormalApplication
        self.app = app_class(device, IPv4Address(f"{self.address}:{self.port}"))
        for obj in self.objects:
            self.app.add_object(obj)
        self._task = asyncio.create_task(self._update())
        log.info("Simulated BACnet device %s on %s", self.instance, self.endpoint)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self.app:
            self.app.close()
            self.app = None

    async def _update(self) -> None:
        while True:
            await asyncio.sleep(self.update_interval_s)
            phase = (time.time() % 120.0) / 120.0 * 2 * math.pi
            self.room_temp.presentValue = round(21.5 + 1.5 * math.sin(phase), 2)
            self.fan_status.presentValue = self.fan_command.presentValue

    # bacpypes3 works out status flags the way a real device does: the fault flag from
    # reliability, and the out-of-service flag from out-of-service.
    @staticmethod
    def set_fault(obj, on: bool = True) -> None:
        obj.reliability = "unreliableOther" if on else "noFaultDetected"

    @staticmethod
    def set_out_of_service(obj, on: bool = True) -> None:
        obj.outOfService = on


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Simulated BACnet/IP device")
    parser.add_argument("--address", default="127.0.0.1/8", help="local address with network size")
    parser.add_argument("--port", type=int, default=47809)
    parser.add_argument("--instance", type=int, default=1001)
    parser.add_argument("--no-rpm", action="store_true", help="refuse ReadPropertyMultiple")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sim = BacnetSimulator(args.address, args.port, args.instance, no_rpm=args.no_rpm)
    await sim.start()
    try:
        await asyncio.Event().wait()
    finally:
        await sim.stop()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
