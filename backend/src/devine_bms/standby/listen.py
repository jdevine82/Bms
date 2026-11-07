"""Listening to a serial bus without sending (milestone 11, decision 4).

Before the HMI takes over, each serial bus must be clear: on a Modbus RTU line no bytes for 60
seconds (the server polls every few seconds while it runs); on an MS/TP trunk no frames from
MAC 0, the server's node. The port is opened read-only in effect: nothing is ever written.
"""

from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)

PREAMBLE = b"\x55\xff"


def mstp_sources(buffer: bytearray) -> tuple[list[int], bytearray]:
    """The source MACs of the complete MS/TP frame headers in `buffer`, and the bytes to keep for
    next time. A header is 55 FF, frame type, destination, source, length (2 bytes), header CRC;
    data (with its own CRC) follows when the length is not zero."""
    sources: list[int] = []
    i, n = 0, len(buffer)
    while True:
        j = buffer.find(PREAMBLE, i)
        if j < 0:
            return sources, bytearray(b"\x55" if buffer[-1:] == b"\x55" else b"")
        if n - j < 8:
            return sources, bytearray(buffer[j:])
        header = bytes(buffer[j + 2:j + 8])
        if _header_crc_ok(header):
            sources.append(header[2])
            length = (header[3] << 8) | header[4]
            i = j + 8 + (length + 2 if length else 0)
            if i >= n:
                return sources, bytearray()
        else:
            i = j + 1


def mstp_frame(frame_type: int, dest: int, src: int, data: bytes = b"") -> bytes:
    """A frame as it is on the wire (for tests and tools); the data CRC is not checked here."""
    header = bytes([frame_type, dest, src, len(data) >> 8, len(data) & 0xFF])
    crc = 0xFF
    for b in header:
        crc = _crc8(b, crc)
    out = PREAMBLE + header + bytes([~crc & 0xFF])
    return out + (data + b"\x00\x00" if data else b"")


def _header_crc_ok(header: bytes) -> bool:
    """BACnet MS/TP header CRC (Clause 9 / Annex G): over type, dest, source, length; then the CRC byte."""
    crc = 0xFF
    for b in header[:5]:
        crc = _crc8(b, crc)
    return (~crc & 0xFF) == header[5]


def _crc8(data: int, crc: int) -> int:
    crc ^= data
    crc = crc ^ (crc << 1) ^ (crc << 2) ^ (crc << 3) ^ (crc << 4) ^ (crc << 5) ^ (crc << 6) ^ (crc << 7)
    return (crc & 0xFE) ^ ((crc >> 8) & 1)


class SerialListener:
    """Listens to one serial bus in a thread; never writes."""

    def __init__(self, bus: str, kind: str, port: str, baudrate: int, clock=time.monotonic) -> None:
        self.bus = bus
        self.kind = kind  # modbus_rtu or bacnet_mstp
        self.port = port
        self.baudrate = baudrate
        self.clock = clock
        self.started_at: float | None = None
        self.last_heard: float | None = None  # last server traffic (any byte; MS/TP: a frame from MAC 0)
        self.error: str | None = None
        self.frames = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self.started_at = self.clock()
        self.error = None
        self._thread = threading.Thread(target=self._run, name=f"listen {self.bus}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    def _run(self) -> None:
        import serial

        try:
            ser = serial.serial_for_url(self.port, baudrate=self.baudrate, timeout=0.3, do_not_open=True)
            ser.open()
        except Exception as exc:  # noqa: BLE001 -- shown as the bus's state
            self.error = f"cannot open {self.port}: {exc}"
            return
        buf = bytearray()
        try:
            while not self._stop.is_set():
                data = ser.read(256)
                if not data:
                    continue
                if self.kind == "bacnet_mstp":
                    buf += data
                    sources, buf = mstp_sources(buf)
                    self.frames += len(sources)
                    if 0 in sources:
                        self.last_heard = self.clock()
                else:
                    self.last_heard = self.clock()
        except Exception as exc:  # noqa: BLE001
            self.error = f"{self.port}: {exc}"
        finally:
            ser.close()

    def clear(self, quiet_s: float) -> bool:
        """Quiet for `quiet_s`, counted from when listening began or the last server traffic."""
        if self.error or self.started_at is None:
            return False
        since = max(self.started_at, self.last_heard or self.started_at)
        return self.clock() - since >= quiet_s

    def state(self, quiet_s: float) -> dict:
        now = self.clock()
        return {"bus": self.bus, "kind": self.kind, "port": self.port, "clear": self.clear(quiet_s),
                "heardAgoS": round(now - self.last_heard, 1) if self.last_heard is not None else None,
                "error": self.error}
