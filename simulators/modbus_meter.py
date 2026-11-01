"""Simulated Modbus TCP power meter for development and tests.

Milestone 2 spec, section 3. This is a small self-contained Modbus TCP server
(function codes 03 and 04 only). It deliberately does not use the BMS's own
Modbus code, so a decoding bug in the BMS cannot be hidden by the same bug here.

Register map (holding registers; FC04 returns the same values):

    wire 0-1  float32  big-endian bytes, high word first   active power, kW
    wire 2-3  float32  big-endian bytes, high word first   voltage L1-N, V
    wire 4-5  float32  big-endian bytes, high word first   current L1, A
    wire 6    int16    big-endian, scale 0.001             power factor
    wire 8-9  uint32   big-endian bytes, LOW word first,   import energy, kWh
                       scale 0.1

Fault modes (set with MeterSimulator.mode, or --mode on the command line):

    normal     answer requests with live values
    down       accept connections but never answer (the client times out)
    exception  answer every read with Modbus exception 04 (server device failure)
    frozen     answer normally, but stop updating the values (tests Stale)

Run:  python simulators/modbus_meter.py --port 5020
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import random
import struct
import time

log = logging.getLogger("modbus_meter")

REGISTER_COUNT = 16
FC_READ_HOLDING = 0x03
FC_READ_INPUT = 0x04
EXC_ILLEGAL_FUNCTION = 0x01
EXC_ILLEGAL_ADDRESS = 0x02
EXC_DEVICE_FAILURE = 0x04
MODES = ("normal", "down", "exception", "frozen")


def float32_high_word_first(value: float) -> list[int]:
    raw = struct.pack(">f", value)
    return [int.from_bytes(raw[0:2], "big"), int.from_bytes(raw[2:4], "big")]


def int16(value: int) -> int:
    return struct.unpack(">H", struct.pack(">h", value))[0]


def uint32_low_word_first(value: int) -> list[int]:
    return [value & 0xFFFF, (value >> 16) & 0xFFFF]


class MeterModel:
    """Produces realistic-looking meter values."""

    def __init__(self, seed: int | None = None, start_energy_kwh: float = 12345.6) -> None:
        self._rng = random.Random(seed)
        self.power_kw = 0.0
        self.voltage_v = 0.0
        self.current_a = 0.0
        self.power_factor = 0.0
        self.energy_kwh = start_energy_kwh
        self._last = time.monotonic()
        self.update()

    def update(self) -> None:
        now = time.monotonic()
        elapsed_h = (now - self._last) / 3600.0
        self._last = now
        # A load pattern that repeats every 10 minutes, so short test runs see it move.
        phase = (time.time() % 600.0) / 600.0 * 2.0 * math.pi
        self.power_kw = round(12.0 + 6.0 * math.sin(phase) + self._rng.uniform(-0.3, 0.3), 3)
        self.voltage_v = round(240.0 + self._rng.uniform(-2.0, 2.0), 2)
        self.power_factor = round(0.92 + self._rng.uniform(-0.03, 0.03), 3)
        self.current_a = round(self.power_kw * 1000.0 / (self.voltage_v * self.power_factor), 2)
        self.energy_kwh += self.power_kw * elapsed_h

    def registers(self) -> list[int]:
        regs = [0] * REGISTER_COUNT
        regs[0:2] = float32_high_word_first(self.power_kw)
        regs[2:4] = float32_high_word_first(self.voltage_v)
        regs[4:6] = float32_high_word_first(self.current_a)
        regs[6] = int16(round(self.power_factor * 1000))
        regs[8:10] = uint32_low_word_first(int(self.energy_kwh * 10))
        return regs


class MeterSimulator:
    """A Modbus TCP server presenting one simulated meter."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5020,
        unit_id: int = 1,
        update_interval_s: float = 1.0,
        seed: int | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.update_interval_s = update_interval_s
        self.model = MeterModel(seed=seed)
        self.mode = "normal"
        self.requests_served = 0
        self._server: asyncio.base_events.Server | None = None
        self._updater: asyncio.Task | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        sock = self._server.sockets[0]
        self.port = sock.getsockname()[1]
        self._updater = asyncio.create_task(self._update_loop())
        log.info("Simulated meter listening on %s:%s unit %s", self.host, self.port, self.unit_id)

    async def stop(self) -> None:
        if self._updater:
            self._updater.cancel()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _update_loop(self) -> None:
        while True:
            await asyncio.sleep(self.update_interval_s)
            if self.mode != "frozen":
                self.model.update()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                header = await reader.readexactly(7)
                transaction_id, protocol_id, length, unit_id = struct.unpack(">HHHB", header)
                pdu = await reader.readexactly(length - 1)
                if protocol_id != 0:
                    continue
                if self.mode == "down":
                    continue  # never answer; the client must time out
                response = self.handle_pdu(unit_id, pdu)
                if response is None:
                    continue
                writer.write(struct.pack(">HHHB", transaction_id, 0, len(response) + 1, unit_id) + response)
                await writer.drain()
                self.requests_served += 1
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            writer.close()

    def handle_pdu(self, unit_id: int, pdu: bytes) -> bytes | None:
        """Return the response PDU for a request PDU, or None to stay silent."""
        if unit_id not in (self.unit_id, 0) or not pdu:
            return None
        function = pdu[0]
        if function not in (FC_READ_HOLDING, FC_READ_INPUT):
            return bytes([function | 0x80, EXC_ILLEGAL_FUNCTION])
        if len(pdu) != 5:
            return bytes([function | 0x80, EXC_ILLEGAL_ADDRESS])
        address, count = struct.unpack(">HH", pdu[1:5])
        if count < 1 or count > 125 or address + count > REGISTER_COUNT:
            return bytes([function | 0x80, EXC_ILLEGAL_ADDRESS])
        if self.mode == "exception":
            return bytes([function | 0x80, EXC_DEVICE_FAILURE])
        values = self.model.registers()[address : address + count]
        return bytes([function, count * 2]) + struct.pack(f">{count}H", *values)


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Simulated Modbus TCP power meter")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5020)
    parser.add_argument("--unit-id", type=int, default=1)
    parser.add_argument("--mode", choices=MODES, default="normal")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sim = MeterSimulator(args.host, args.port, args.unit_id, seed=args.seed)
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
