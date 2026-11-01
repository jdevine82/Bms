"""Milestone 2 spec section 9, integration checks 1 to 6, against the simulated meter."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from devine_bms.runtime import Runtime
from devine_bms.storage.base import MemoryStorage
from devine_bms.values import Status
from simulators.modbus_meter import MeterSimulator
from helpers import TEST_INTERVALS, example_data, runtime_config_from


@pytest.fixture
async def sim():
    simulator = MeterSimulator(port=0, update_interval_s=0.05, seed=3)
    await simulator.start()
    yield simulator
    await simulator.stop()


async def start_runtime(sim, mutate=None):
    data = example_data(sim.port)
    if mutate:
        mutate(data)
    storage = MemoryStorage(runtime_config_from(data))
    runtime = Runtime(storage, "dev_site", intervals=TEST_INTERVALS, stale_check_s=0.05, flush_interval_s=0.1)
    await runtime.start()
    return runtime, storage


def by_name(runtime):
    return {p.name: p for p in runtime.config.points}


async def wait_for(condition, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.05)


def statuses(runtime):
    return {p.name: runtime.cache.get(p.id).status for p in runtime.config.points}


async def test_1_all_points_good_and_match_the_simulator(sim):
    runtime, _ = await start_runtime(sim)
    try:
        await wait_for(lambda: set(statuses(runtime).values()) == {Status.OK})
        points = by_name(runtime)
        power = runtime.cache.get(points["site_meter1_power"].id).value
        assert 5.0 < power < 19.0
        pf = runtime.cache.get(points["site_meter1_pf"].id).value
        assert pf == pytest.approx(sim.model.power_factor, abs=0.05)
        energy = runtime.cache.get(points["site_meter1_import_energy"].id).value
        assert energy == pytest.approx(sim.model.energy_kwh, abs=0.2)
        assert runtime.pollers[0].online is True
    finally:
        await runtime.stop()


async def test_2_history_is_written_and_read_back(sim):
    runtime, storage = await start_runtime(sim)
    try:
        await wait_for(lambda: len(storage.rows) >= 10)
    finally:
        await runtime.stop()
    point = by_name(runtime)["site_meter1_power"]
    now = datetime.now(UTC)
    rows = await storage.read_history(point.id, now - timedelta(minutes=1), now + timedelta(seconds=1))
    assert len(rows) >= 2
    assert all(r.status == Status.OK and r.num is not None for r in rows)
    assert rows == sorted(rows, key=lambda r: r.ts)


async def test_3_no_answer_makes_points_down_and_is_recorded(sim):
    runtime, storage = await start_runtime(sim)
    try:
        await wait_for(lambda: set(statuses(runtime).values()) == {Status.OK})
        sim.mode = "down"
        await wait_for(lambda: set(statuses(runtime).values()) == {Status.DOWN})
        await asyncio.sleep(0.3)
    finally:
        await runtime.stop()
    power = by_name(runtime)["site_meter1_power"]
    down_rows = [r for r in storage.rows if r.point_id == power.id and r.status == Status.DOWN]
    assert len(down_rows) == 1  # one row for the change, not one per failed poll
    assert "no answer" in down_rows[0].reason


async def test_4_stale_when_no_new_value_in_time(sim):
    # Slow class polls every 0.5 s in tests; a 0.2 s stale time means it goes Stale between polls.
    def short_stale(data):
        data["points"][4]["staleAfterS"] = 0.2

    runtime, storage = await start_runtime(sim, short_stale)
    try:
        energy = by_name(runtime)["site_meter1_import_energy"]
        await wait_for(lambda: runtime.cache.get(energy.id).status == Status.STALE)
        assert "no new value" in runtime.cache.get(energy.id).reason
        await wait_for(lambda: runtime.cache.get(energy.id).status == Status.OK)  # next poll brings it back
    finally:
        await runtime.stop()


async def test_5_modbus_exception_makes_points_fault(sim):
    runtime, _ = await start_runtime(sim)
    try:
        await wait_for(lambda: set(statuses(runtime).values()) == {Status.OK})
        sim.mode = "exception"
        await wait_for(lambda: set(statuses(runtime).values()) == {Status.FAULT})
        reason = runtime.cache.get(by_name(runtime)["site_meter1_power"].id).reason
        assert "exception 4" in reason
    finally:
        await runtime.stop()


async def test_6_recovers_without_restart(sim):
    runtime, _ = await start_runtime(sim)
    try:
        await wait_for(lambda: set(statuses(runtime).values()) == {Status.OK})
        port = sim.port
        await sim.stop()  # the device disappears completely
        await wait_for(lambda: set(statuses(runtime).values()) == {Status.DOWN})
        assert runtime.pollers[0].online is False
        restarted = MeterSimulator(port=port, update_interval_s=0.05)
        await restarted.start()
        try:
            await wait_for(lambda: set(statuses(runtime).values()) == {Status.OK}, timeout=10)
            assert runtime.pollers[0].online is True
        finally:
            await restarted.stop()
    finally:
        await runtime.stop()


async def test_block_reads_keep_requests_low(sim):
    runtime, _ = await start_runtime(sim)
    try:
        await asyncio.sleep(1.0)
    finally:
        await runtime.stop()
    # One block per speed class (fast, normal, slow): about 10 + 5 + 2 reads in 1 s,
    # not one read per point.
    assert sim.requests_served < 30
