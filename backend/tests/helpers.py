"""Shared test helpers: build a runtime config for the simulated meter without a database."""

from __future__ import annotations

import json
from pathlib import Path

from devine_bms.config.model import PollClass
from devine_bms.config.validate import parse_config
from devine_bms.runtime_model import RuntimeConfig, from_site_config

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
    return from_site_config(config)
