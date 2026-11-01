import math
import struct

import pytest

from devine_bms.drivers.modbus.codec import ByteOrder, DataType, DecodeError, WordOrder, decode_registers
from simulators import modbus_meter


def words(raw: bytes) -> list[int]:
    return [int.from_bytes(raw[i : i + 2], "big") for i in range(0, len(raw), 2)]


@pytest.mark.parametrize(
    ("data_type", "fmt", "value"),
    [
        (DataType.INT16, ">h", -1234),
        (DataType.UINT16, ">H", 65000),
        (DataType.INT32, ">i", -123456789),
        (DataType.UINT32, ">I", 4000000000),
        (DataType.FLOAT32, ">f", 12.5),
    ],
)
def test_big_endian_high_word_first(data_type, fmt, value):
    assert decode_registers(words(struct.pack(fmt, value)), data_type) == value


def test_low_word_first_swaps_registers():
    regs = words(struct.pack(">I", 0x12345678))  # [0x1234, 0x5678]
    assert decode_registers(list(reversed(regs)), DataType.UINT32, word_order=WordOrder.LOW_FIRST) == 0x12345678


def test_little_endian_bytes_inside_each_register():
    regs = [0x3412]  # bytes swapped inside the register
    assert decode_registers(regs, DataType.UINT16, byte_order=ByteOrder.LITTLE) == 0x1234


def test_float_with_both_orders():
    raw = struct.pack(">f", -7.25)
    regs = [int.from_bytes(raw[2:4], "little"), int.from_bytes(raw[0:2], "little")]
    value = decode_registers(regs, DataType.FLOAT32, byte_order=ByteOrder.LITTLE, word_order=WordOrder.LOW_FIRST)
    assert value == -7.25


def test_scale_and_offset():
    assert decode_registers([925], DataType.INT16, scale=0.001) == pytest.approx(0.925)
    assert decode_registers([100], DataType.UINT16, scale=0.5, offset=-40) == 10


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_float_is_a_fault(value):
    with pytest.raises(DecodeError):
        decode_registers(words(struct.pack(">f", value)), DataType.FLOAT32)


def test_wrong_register_count_is_a_fault():
    with pytest.raises(DecodeError):
        decode_registers([1], DataType.FLOAT32)


def test_bit_is_not_a_register_type():
    with pytest.raises(DecodeError):
        decode_registers([1], DataType.BIT)


def test_decodes_the_simulator_register_map():
    """The BMS decoder and the simulator's independent encoder must agree (milestone 2 spec 3)."""
    model = modbus_meter.MeterModel(seed=1)
    regs = model.registers()
    assert decode_registers(regs[0:2], DataType.FLOAT32) == pytest.approx(model.power_kw, abs=1e-3)
    assert decode_registers(regs[2:4], DataType.FLOAT32) == pytest.approx(model.voltage_v, abs=1e-3)
    assert decode_registers(regs[4:6], DataType.FLOAT32) == pytest.approx(model.current_a, abs=1e-3)
    assert decode_registers(regs[6:7], DataType.INT16, scale=0.001) == pytest.approx(model.power_factor)
    energy = decode_registers(regs[8:10], DataType.UINT32, word_order=WordOrder.LOW_FIRST, scale=0.1)
    assert energy == pytest.approx(int(model.energy_kwh * 10) / 10)
