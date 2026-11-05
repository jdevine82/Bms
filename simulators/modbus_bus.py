"""Simulated Modbus devices for development and tests (milestone 2 spec 3, milestone 4a).

A small self-contained Modbus server. It deliberately does not use pymodbus or
the BMS's own Modbus code, so a decoding bug in the BMS cannot be hidden by the
same bug here.

One server is one "bus": it can hold several devices (unit IDs), like an RS485
line or a serial-to-Ethernet gateway. Framing:

    tcp  Modbus TCP (MBAP header). Also what a gateway with TCP framing looks like.
    rtu  Modbus RTU frames with CRC, over a TCP connection. This is what an
         "RTU over TCP" gateway looks like, and what a serial port carries; tests
         reach it through pyserial's socket:// ports or a socat pseudo-terminal.

Devices:

    MeterModel     power meter, holding/input registers (register map below)
    IoModuleModel  8 discrete inputs and 18 coils, like the CWT-MB328B(II) (H1)

Meter register map (holding registers; FC04 returns the same values):

    wire 0-1  float32  big-endian bytes, high word first   active power, kW
    wire 2-3  float32  big-endian bytes, high word first   voltage L1-N, V
    wire 4-5  float32  big-endian bytes, high word first   current L1, A
    wire 6    int16    big-endian, scale 0.001             power factor
    wire 8-9  uint32   big-endian bytes, LOW word first,   import energy, kWh
                       scale 0.1

Fault modes, for the whole bus (`bus.mode`) or one device (`bus.unit_modes[id]`):

    normal     answer requests with live values
    down       never answer (the client times out)
    exception  answer every read with Modbus exception 04 (server device failure)
    frozen     answer normally, but stop updating the values
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import struct
import time

log = logging.getLogger("modbus_sim")

FC_READ_COILS = 0x01
FC_READ_DISCRETE = 0x02
FC_READ_HOLDING = 0x03
FC_READ_INPUT = 0x04
FC_DEVICE_ID = 0x2B  # Read Device Identification (MEI type 0x0E)
EXC_ILLEGAL_FUNCTION = 0x01
EXC_ILLEGAL_ADDRESS = 0x02
EXC_DEVICE_FAILURE = 0x04
EXC_GATEWAY_TARGET = 0x0B
MODES = ("normal", "down", "exception", "frozen")


def crc16(data: bytes) -> int:
    """Modbus RTU CRC-16 (polynomial 0xA001, initial 0xFFFF)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def with_crc(frame: bytes) -> bytes:
    return frame + struct.pack("<H", crc16(frame))  # CRC is sent low byte first


def float32_high_word_first(value: float) -> list[int]:
    raw = struct.pack(">f", value)
    return [int.from_bytes(raw[0:2], "big"), int.from_bytes(raw[2:4], "big")]


def int16(value: int) -> int:
    return struct.unpack(">H", struct.pack(">h", value))[0]


def uint32_low_word_first(value: int) -> list[int]:
    return [value & 0xFFFF, (value >> 16) & 0xFFFF]


class MeterModel:
    """Produces realistic-looking meter values."""

    register_count = 16
    bit_counts: dict[int, int] = {}
    # Answers Read Device Identification: vendor, product code, revision.
    identity = ("Devine BMS simulator", "SIM-METER", "1.0")

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
        regs = [0] * self.register_count
        regs[0:2] = float32_high_word_first(self.power_kw)
        regs[2:4] = float32_high_word_first(self.voltage_v)
        regs[4:6] = float32_high_word_first(self.current_a)
        regs[6] = int16(round(self.power_factor * 1000))
        regs[8:10] = uint32_low_word_first(int(self.energy_kwh * 10))
        return regs

    def bits(self, function: int) -> list[bool]:
        return []


class IoModuleModel:
    """8 discrete inputs (DI0-DI7, FC02) and 18 coils (DO0-DO17, FC01), like the H1 module.

    Inputs follow a slow repeating pattern so changes are visible; coils keep the
    states the test sets in `coils` (the BMS does not write yet).
    """

    register_count = 0
    bit_counts = {FC_READ_DISCRETE: 8, FC_READ_COILS: 18}

    def __init__(self, seed: int | None = None) -> None:
        self.inputs = [False] * 8
        self.coils = [False] * 18
        self.coils[0] = True
        self._seed = seed or 0
        self.update()

    def update(self) -> None:
        step = int(time.time() // 5) + self._seed
        self.inputs = [bool((step >> i) & 1) for i in range(8)]

    def registers(self) -> list[int]:
        return []

    def bits(self, function: int) -> list[bool]:
        return list(self.coils) if function == FC_READ_COILS else list(self.inputs)


class ModbusSimulator:
    """A Modbus server (one bus) holding one or more simulated devices."""

    def __init__(
        self,
        units: dict[int, MeterModel | IoModuleModel],
        host: str = "127.0.0.1",
        port: int = 5020,
        framing: str = "tcp",
        update_interval_s: float = 1.0,
        gateway: bool = False,
    ) -> None:
        if framing not in ("tcp", "rtu"):
            raise ValueError("framing must be 'tcp' or 'rtu'")
        self.units = units
        self.host = host
        self.port = port
        self.framing = framing
        self.update_interval_s = update_interval_s
        # Act like a Modbus TCP-to-RTU gateway: a device that does not answer gets
        # exception 11 (gateway target device failed to respond) instead of silence.
        self.gateway = gateway
        self.mode = "normal"
        self.unit_modes: dict[int, str] = {}
        self.requests_served = 0
        self._server: asyncio.base_events.Server | None = None
        self._updater: asyncio.Task | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    def mode_for(self, unit_id: int) -> str:
        return self.mode if self.mode != "normal" else self.unit_modes.get(unit_id, "normal")

    async def start(self) -> None:
        handler = self._handle_tcp if self.framing == "tcp" else self._handle_rtu
        self._server = await asyncio.start_server(handler, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        self._updater = asyncio.create_task(self._update_loop())
        log.info("Simulated Modbus %s bus on %s:%s, units %s", self.framing, self.host, self.port, sorted(self.units))

    async def stop(self) -> None:
        """Stop listening and drop every open connection (clients see the bus go away)."""
        if self._updater:
            self._updater.cancel()
        if self._server:
            self._server.close()
            for writer in list(self._writers):
                writer.close()
            await self._server.wait_closed()
            self._server = None

    async def _update_loop(self) -> None:
        while True:
            await asyncio.sleep(self.update_interval_s)
            for unit_id, model in self.units.items():
                if self.mode_for(unit_id) != "frozen":
                    model.update()

    async def _handle_tcp(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        try:
            while True:
                header = await reader.readexactly(7)
                transaction_id, protocol_id, length, unit_id = struct.unpack(">HHHB", header)
                pdu = await reader.readexactly(length - 1)
                if protocol_id != 0:
                    continue
                response = self.handle_pdu(unit_id, pdu)
                if response is None:
                    continue
                writer.write(struct.pack(">HHHB", transaction_id, 0, len(response) + 1, unit_id) + response)
                await writer.drain()
                self.requests_served += 1
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()

    async def _handle_rtu(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """RTU frames over a byte stream. The frame length depends on the function, as a real
        device works it out: functions 1 to 6 are 8 bytes, 43 (device identification) is 7,
        15 and 16 carry a byte count."""
        self._writers.add(writer)
        try:
            while True:
                head = await reader.readexactly(2)
                function = head[1]
                if function in (1, 2, 3, 4, 5, 6):
                    rest = await reader.readexactly(6)
                elif function == FC_DEVICE_ID:
                    rest = await reader.readexactly(5)
                elif function in (15, 16):
                    fixed = await reader.readexactly(5)
                    rest = fixed + await reader.readexactly(fixed[4] + 2)
                else:
                    # Unknown length: drop what has arrived, as a device would after a garbled frame.
                    try:
                        await asyncio.wait_for(reader.read(256), 0.05)
                    except TimeoutError:
                        pass
                    continue
                frame = head + rest
                if crc16(frame[:-2]) != struct.unpack("<H", frame[-2:])[0]:
                    log.debug("bad CRC, frame ignored")
                    continue  # a real device stays silent on a bad CRC
                unit_id, pdu = frame[0], frame[1:-2]
                response = self.handle_pdu(unit_id, pdu)
                if response is None or unit_id == 0:
                    continue  # no answer to broadcasts
                writer.write(with_crc(bytes([unit_id]) + response))
                await writer.drain()
                self.requests_served += 1
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()

    def handle_pdu(self, unit_id: int, pdu: bytes) -> bytes | None:
        """Return the response PDU for a request PDU, or None to stay silent."""
        model = self.units.get(unit_id)
        mode = self.mode_for(unit_id)
        if model is None or not pdu or mode == "down":
            if self.gateway and pdu and self.mode != "down":
                return bytes([pdu[0] | 0x80, EXC_GATEWAY_TARGET])
            return None  # no device at that address answers
        function = pdu[0]
        if function == FC_DEVICE_ID:
            identity = getattr(model, "identity", None)
            if not identity or len(pdu) < 4 or pdu[1] != 0x0E:
                return bytes([function | 0x80, EXC_ILLEGAL_FUNCTION])
            objects = b"".join(bytes([i, len(v)]) + v.encode() for i, v in enumerate(identity))
            return bytes([function, 0x0E, pdu[2], 0x01, 0x00, 0x00, len(identity)]) + objects
        if function not in (FC_READ_COILS, FC_READ_DISCRETE, FC_READ_HOLDING, FC_READ_INPUT):
            return bytes([function | 0x80, EXC_ILLEGAL_FUNCTION])
        if len(pdu) != 5:
            return bytes([function | 0x80, EXC_ILLEGAL_ADDRESS])
        address, count = struct.unpack(">HH", pdu[1:5])
        if function in (FC_READ_COILS, FC_READ_DISCRETE):
            size = model.bit_counts.get(function, 0)
            if count < 1 or count > 2000 or address + count > size:
                return bytes([function | 0x80, EXC_ILLEGAL_ADDRESS])
            if mode == "exception":
                return bytes([function | 0x80, EXC_DEVICE_FAILURE])
            bits = model.bits(function)[address : address + count]
            packed = bytearray((count + 7) // 8)
            for i, bit in enumerate(bits):
                if bit:
                    packed[i // 8] |= 1 << (i % 8)
            return bytes([function, len(packed)]) + bytes(packed)
        if count < 1 or count > 125 or address + count > model.register_count:
            return bytes([function | 0x80, EXC_ILLEGAL_ADDRESS])
        if mode == "exception":
            return bytes([function | 0x80, EXC_DEVICE_FAILURE])
        values = model.registers()[address : address + count]
        return bytes([function, count * 2]) + struct.pack(f">{count}H", *values)
