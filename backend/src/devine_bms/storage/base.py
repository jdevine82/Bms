"""Storage interface (main spec 3.1, milestone 2 spec 6).

Full mode uses PostgreSQL + TimescaleDB (storage/postgres.py). Standalone mode
will add a config-file and in-memory implementation later. MemoryStorage here is
for tests.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from devine_bms.runtime_model import RuntimeConfig
from devine_bms.values import HisRow


class Storage(Protocol):
    async def load_runtime_config(self, site_name: str) -> RuntimeConfig:
        """Enabled devices and points for the site."""

    async def write_history(self, rows: Sequence[HisRow]) -> None:
        """Append history rows. Rows with an existing (point, time) are ignored."""

    async def read_history(self, point_id: uuid.UUID, start: datetime, end: datetime) -> list[HisRow]:
        """History rows with start <= ts < end, oldest first."""


class MemoryStorage:
    """In-memory storage for tests."""

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.rows: list[HisRow] = []

    async def load_runtime_config(self, site_name: str) -> RuntimeConfig:
        return self.config

    async def write_history(self, rows: Sequence[HisRow]) -> None:
        seen = {(r.point_id, r.ts) for r in self.rows}
        self.rows.extend(r for r in rows if (r.point_id, r.ts) not in seen)

    async def read_history(self, point_id: uuid.UUID, start: datetime, end: datetime) -> list[HisRow]:
        return sorted((r for r in self.rows if r.point_id == point_id and start <= r.ts < end), key=lambda r: r.ts)
