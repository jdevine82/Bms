"""The simulated meter answers a real Modbus client, and its fault modes work."""

import asyncio

import pytest
from pymodbus.client import AsyncModbusTcpClient

from devine_bms.drivers.modbus.codec import DataType, decode_registers
from simulators.modbus_meter import MeterSimulator


@pytest.fixture
async def sim():
    simulator = MeterSimulator(port=0, update_interval_s=0.05, seed=7)
    await simulator.start()
    yield simulator
    await simulator.stop()


@pytest.fixture
async def client(sim):
    c = AsyncModbusTcpClient("127.0.0.1", port=sim.port, timeout=0.5, retries=0)
    await c.connect()
    yield c
    c.close()


async def test_reads_power(sim, client):
    result = await client.read_holding_registers(0, count=10, device_id=1)
    assert not result.isError()
    power = decode_registers(result.registers[0:2], DataType.FLOAT32)
    assert 5.0 < power < 19.0


async def test_input_registers_match(sim, client):
    result = await client.read_input_registers(6, count=1, device_id=1)
    assert not result.isError()
    assert 0.85 < decode_registers(result.registers, DataType.INT16, scale=0.001) < 1.0


async def test_out_of_range_is_illegal_address(sim, client):
    result = await client.read_holding_registers(22, count=4, device_id=1)  # the meter has 24 registers
    assert result.isError()
    assert result.exception_code == 2


async def test_exception_mode(sim, client):
    sim.mode = "exception"
    result = await client.read_holding_registers(0, count=2, device_id=1)
    assert result.isError()
    assert result.exception_code == 4


async def test_down_mode_never_answers(sim, client):
    sim.mode = "down"
    with pytest.raises(Exception):
        result = await client.read_holding_registers(0, count=2, device_id=1)
        assert result.isError()  # some pymodbus versions return an error instead of raising


async def test_frozen_mode_stops_updates(sim, client):
    sim.mode = "frozen"
    first = (await client.read_holding_registers(0, count=10, device_id=1)).registers
    await asyncio.sleep(0.3)
    second = (await client.read_holding_registers(0, count=10, device_id=1)).registers
    assert first == second


async def test_values_change_when_running(sim, client):
    first = (await client.read_holding_registers(2, count=2, device_id=1)).registers
    for _ in range(20):
        await asyncio.sleep(0.06)
        if (await client.read_holding_registers(2, count=2, device_id=1)).registers != first:
            return
    pytest.fail("voltage never changed")
