"""Decode Modbus register values into point values (main spec 5.1).

Each point's address says how its registers are laid out: the data type, the
byte order inside each 16-bit register, the order of the registers (words) for
32-bit types, and a scale and offset applied after decoding.
"""

from __future__ import annotations

import math
import struct
from enum import StrEnum


class DataType(StrEnum):
    BIT = "bit"
    INT16 = "int16"
    UINT16 = "uint16"
    INT32 = "int32"
    UINT32 = "uint32"
    FLOAT32 = "float32"


class ByteOrder(StrEnum):
    BIG = "big"  # most significant byte first inside each register (the Modbus standard)
    LITTLE = "little"


class WordOrder(StrEnum):
    HIGH_FIRST = "high_first"  # the register holding the high 16 bits comes first
    LOW_FIRST = "low_first"


REGISTER_COUNT = {
    DataType.BIT: 1,
    DataType.INT16: 1,
    DataType.UINT16: 1,
    DataType.INT32: 2,
    DataType.UINT32: 2,
    DataType.FLOAT32: 2,
}

_STRUCT_FORMAT = {
    DataType.INT16: ">h",
    DataType.UINT16: ">H",
    DataType.INT32: ">i",
    DataType.UINT32: ">I",
    DataType.FLOAT32: ">f",
}


class DecodeError(ValueError):
    """The registers could not be turned into a valid value (reported as a fault)."""


def registers_needed(data_type: DataType) -> int:
    return REGISTER_COUNT[DataType(data_type)]


def decode_registers(
    registers: list[int],
    data_type: DataType,
    byte_order: ByteOrder = ByteOrder.BIG,
    word_order: WordOrder = WordOrder.HIGH_FIRST,
    scale: float = 1.0,
    offset: float = 0.0,
) -> float:
    """Decode register words into a number, then apply scale and offset.

    `value = raw * scale + offset`. Raises DecodeError for a wrong register count
    or a non-finite float (NaN or infinity), which the BMS marks as Bad (fault).
    """
    data_type = DataType(data_type)
    if data_type == DataType.BIT:
        raise DecodeError("bit values come from coils or discrete inputs; use decode_bit")
    needed = REGISTER_COUNT[data_type]
    if len(registers) != needed:
        raise DecodeError(f"{data_type} needs {needed} registers, got {len(registers)}")
    for word in registers:
        if not 0 <= word <= 0xFFFF:
            raise DecodeError(f"register value {word} is outside 0 to 65535")

    words = list(registers)
    if needed == 2 and WordOrder(word_order) == WordOrder.LOW_FIRST:
        words.reverse()
    raw_bytes = b"".join(
        w.to_bytes(2, "big" if ByteOrder(byte_order) == ByteOrder.BIG else "little") for w in words
    )
    raw = struct.unpack(_STRUCT_FORMAT[data_type], raw_bytes)[0]
    if isinstance(raw, float) and not math.isfinite(raw):
        raise DecodeError(f"{data_type} decoded to {raw}")
    return raw * scale + offset


def decode_bit(value: bool | int) -> bool:
    """Coils and discrete inputs are already true/false."""
    return bool(value)


def encode_registers(
    value: float,
    data_type: DataType,
    byte_order: ByteOrder = ByteOrder.BIG,
    word_order: WordOrder = WordOrder.HIGH_FIRST,
    scale: float = 1.0,
    offset: float = 0.0,
) -> list[int]:
    """The reverse of decode_registers, for commands (milestone 7A): `raw = (value - offset) / scale`.

    Integer types are rounded to the nearest whole raw value; a value outside the type's range
    raises DecodeError (nothing is written).
    """
    data_type = DataType(data_type)
    if data_type == DataType.BIT:
        raise DecodeError("bit values are written to coils")
    if scale == 0:
        raise DecodeError("the point's scale is 0, so it cannot be written")
    raw: float | int = (value - offset) / scale
    fmt = _STRUCT_FORMAT[data_type]
    if fmt[-1] not in "fd":
        raw = round(raw)
    try:
        raw_bytes = struct.pack(fmt, raw)
    except struct.error as exc:
        raise DecodeError(f"{value} does not fit a {data_type} register value") from exc
    words = [int.from_bytes(raw_bytes[i:i + 2], "big" if ByteOrder(byte_order) == ByteOrder.BIG else "little")
             for i in range(0, len(raw_bytes), 2)]
    if len(words) == 2 and WordOrder(word_order) == WordOrder.LOW_FIRST:
        words.reverse()
    return words
