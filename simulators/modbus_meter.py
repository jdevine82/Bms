"""Simulated Modbus TCP power meter, and a command line for simulated buses.

The devices and the server live in simulators/modbus_bus.py. This file keeps the
original names (MeterModel, MeterSimulator) used by the tests.

Run a single meter (Modbus TCP):
    python simulators/modbus_meter.py --port 5020

Run an RTU bus (RTU framing over TCP, as an "RTU over TCP" gateway) with a meter
at unit 1 and an H1-style I/O module at unit 2:
    python simulators/modbus_meter.py --port 5021 --framing rtu --units meter:1,io:2

`--mode down | exception | frozen` starts it in a fault mode.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simulators.modbus_bus import (  # noqa: E402
    MODES,
    IoModuleModel,
    MeterModel,
    ModbusSimulator,
    float32_high_word_first,
    int16,
    uint32_low_word_first,
)

__all__ = [
    "MeterModel",
    "MeterSimulator",
    "IoModuleModel",
    "ModbusSimulator",
    "float32_high_word_first",
    "int16",
    "uint32_low_word_first",
]


class MeterSimulator(ModbusSimulator):
    """A Modbus TCP server presenting one simulated meter."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5020,
        unit_id: int = 1,
        update_interval_s: float = 1.0,
        seed: int | None = None,
    ) -> None:
        self.unit_id = unit_id
        self.model = MeterModel(seed=seed)
        super().__init__({unit_id: self.model}, host, port, "tcp", update_interval_s)


def _parse_units(text: str) -> dict[int, MeterModel | IoModuleModel]:
    units: dict[int, MeterModel | IoModuleModel] = {}
    for item in text.split(","):
        kind, _, unit = item.partition(":")
        model = {"meter": MeterModel, "io": IoModuleModel}.get(kind.strip())
        if model is None or not unit.strip().isdigit():
            raise SystemExit(f"bad --units entry '{item}': use meter:<id> or io:<id>")
        units[int(unit)] = model()
    return units


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Simulated Modbus devices")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5020)
    parser.add_argument("--framing", choices=("tcp", "rtu"), default="tcp")
    parser.add_argument("--units", default="meter:1", help="for example meter:1,io:2")
    parser.add_argument("--mode", choices=MODES, default="normal")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sim = ModbusSimulator(_parse_units(args.units), args.host, args.port, args.framing)
    sim.mode = args.mode
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
