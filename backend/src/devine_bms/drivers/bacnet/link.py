"""The BMS's own BACnet/IP application on the device network (milestone 4b).

One per backend. It has its own device object (instance and name from the site's
`bacnet` settings) and is the client every BACnet/IP device is read through.
"""

from __future__ import annotations

import itertools
import logging

from bacpypes3.ipv4.app import NormalApplication
from bacpypes3.local.device import DeviceObject
from bacpypes3.pdu import Address, IPv4Address

from devine_bms.config.model import BacnetSettings

log = logging.getLogger(__name__)

VENDOR_ID = 999  # placeholder until a BACnet vendor ID is assigned (main spec 1: no BACnet claims)


def device_address(address: str) -> Address:
    """`192.168.10.20` or `192.168.10.20:47809` as a bacpypes3 address."""
    return IPv4Address(address if ":" in address else f"{address}:47808")


class BacnetLink:
    def __init__(self, settings: BacnetSettings) -> None:
        self.settings = settings
        self.app: NormalApplication | None = None
        self._process_ids = itertools.count(1)

    async def start(self) -> None:
        device = DeviceObject(
            objectIdentifier=("device", self.settings.device_instance),
            objectName=self.settings.device_name,
            vendorIdentifier=VENDOR_ID,
            maxApduLengthAccepted=1476,
            segmentationSupported="segmentedBoth",
        )
        local = IPv4Address(f"{self.settings.local_address}:{self.settings.port}")
        self.app = NormalApplication(device, local)
        log.info("BACnet/IP link on %s:%s as device %s", self.settings.local_address, self.settings.port,
                 self.settings.device_instance)

    def next_process_id(self) -> int:
        """A subscriber process identifier for a COV subscription, unique in this backend."""
        return next(self._process_ids)

    def close(self) -> None:
        if self.app is not None:
            self.app.close()
            self.app = None
