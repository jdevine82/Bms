"""Shared test helpers: build a runtime config for the simulated meter without a database."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from devine_bms.config.model import PollClass
from devine_bms.config.validate import parse_config
from devine_bms.runtime_model import RuntimeConfig, RuntimeDevice, RuntimePoint

EXAMPLE = Path(__file__).resolve().parents[2] / "config" / "site.example.json"

# Fast intervals so tests run in seconds.
TEST_INTERVALS = {PollClass.FAST: 0.1, PollClass.NORMAL: 0.2, PollClass.SLOW: 0.5}


def example_data(port: int, timeout_s: float = 0.3, retries: int = 0) -> dict:
    data = json.loads(EXAMPLE.read_text())
    conn = data["devices"][0]["connection"]
    conn.update(port=port, timeoutS=timeout_s, retries=retries)
    return data


def runtime_config_from(data: dict) -> RuntimeConfig:
    """What PostgresStorage.load_runtime_config would return, built in memory."""
    config, _ = parse_config(data)
    devices = {
        d.name: RuntimeDevice(uuid.uuid4(), d.name, d.protocol, d.connection, d.enabled)
        for d in config.devices
    }
    points = tuple(
        RuntimePoint(
            id=uuid.uuid4(), name=p.name, device_id=devices[p.device].id, kind=p.kind, unit=p.unit,
            enum=tuple(p.enum) if p.enum else None, address=p.address, poll_class=p.poll_class,
            stale_after_s=p.stale_after_s, his=p.his,
        )
        for p in config.points
    )
    return RuntimeConfig(uuid.uuid4(), config.site.tz, tuple(devices.values()), points)
