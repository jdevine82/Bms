"""BACnet/IP bench tools (milestone 4b): find devices and read one property.

Read-only: these never write to a device.

    uv run python -m devine_bms.drivers.bacnet.tools whois --local 192.168.10.5/24
    uv run python -m devine_bms.drivers.bacnet.tools whois --local 192.168.10.5/24 --to 192.168.10.20
    uv run python -m devine_bms.drivers.bacnet.tools read --local 192.168.10.5/24 \\
        --device 192.168.10.20 --object analog-input,1 [--property present-value]

`--local` is this computer's address with its network size, so the broadcast reaches the
right subnet. Use a free UDP port with `--port` if another BACnet program is running.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from bacpypes3.apdu import ErrorRejectAbortNack

from devine_bms.config.model import BacnetSettings
from devine_bms.drivers.bacnet.link import BacnetLink, device_address

TOOL_INSTANCE = 4194301  # a high instance unlikely to clash with site devices


async def whois(link: BacnetLink, to: str | None, low: int | None, high: int | None, wait_s: float) -> list[dict]:
    target = device_address(to) if to else None
    answers = await link.app.who_is(low, high, target, timeout=wait_s)
    devices = []
    for i_am in answers:
        devices.append({
            "instance": i_am.iAmDeviceIdentifier[1],
            "address": str(i_am.pduSource),
            "vendor": int(i_am.vendorID),
            "maxApdu": int(i_am.maxAPDULengthAccepted),
            "segmentation": str(i_am.segmentationSupported),
        })
    return sorted(devices, key=lambda d: d["instance"])


async def read(link: BacnetLink, device: str, obj: str, prop: str, wait_s: float):
    return await asyncio.wait_for(link.app.read_property(device_address(device), obj, prop), wait_s)


async def _run(args) -> int:
    link = BacnetLink(BacnetSettings(local_address=args.local, port=args.port, device_instance=TOOL_INSTANCE,
                                     device_name="Devine BMS bench tool"))
    await link.start()
    try:
        if args.command == "whois":
            devices = await whois(link, args.to, args.low, args.high, args.wait)
            if not devices:
                print("No devices answered.")
            for d in devices:
                print(f"device {d['instance']:>8}  {d['address']:22} vendor {d['vendor']:<5} "
                      f"max APDU {d['maxApdu']:<5} segmentation {d['segmentation']}")
            return 0
        try:
            value = await read(link, args.device, args.object, args.property, args.wait)
        except TimeoutError:
            print(f"No answer from {args.device} within {args.wait:g} s.", file=sys.stderr)
            return 1
        except ErrorRejectAbortNack as exc:
            print(f"BACnet error: {exc}", file=sys.stderr)
            return 1
        print(f"{args.object} {args.property} = {value}")
        return 0
    finally:
        link.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BACnet/IP bench tools (read-only)")
    parser.add_argument("--local", required=True, help="this computer's address and network size, e.g. 192.168.10.5/24")
    parser.add_argument("--port", type=int, default=47808)
    parser.add_argument("--wait", type=float, default=3.0, help="seconds to wait for answers")
    sub = parser.add_subparsers(dest="command", required=True)
    w = sub.add_parser("whois", help="find BACnet devices")
    w.add_argument("--to", help="ask one address instead of broadcasting")
    w.add_argument("--low", type=int)
    w.add_argument("--high", type=int)
    r = sub.add_parser("read", help="read one property")
    r.add_argument("--device", required=True, help="device address, e.g. 192.168.10.20 or 192.168.10.20:47809")
    r.add_argument("--object", required=True, help="e.g. analog-input,1")
    r.add_argument("--property", default="present-value")
    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
