"""Batch history rows into storage (milestone 2 spec 4)."""

from __future__ import annotations

import asyncio
import logging
from collections import deque

from devine_bms.storage.base import Storage
from devine_bms.values import HisRow

log = logging.getLogger(__name__)

MAX_PENDING = 100_000  # rows kept in memory while storage is unavailable


class HistoryWriter:
    def __init__(self, storage: Storage, flush_interval_s: float = 1.0, batch_size: int = 500) -> None:
        self.storage = storage
        self.flush_interval_s = flush_interval_s
        self.batch_size = batch_size
        self.pending: deque[HisRow] = deque(maxlen=MAX_PENDING)
        self.written = 0
        self._storage_ok = True

    def add(self, row: HisRow) -> None:
        if len(self.pending) == MAX_PENDING:
            log.error("history buffer full; dropping the oldest row")
        self.pending.append(row)

    async def run(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.flush_interval_s)
                await self.flush()
        finally:
            await self.flush()

    async def flush(self) -> None:
        while self.pending:
            batch = [self.pending[i] for i in range(min(self.batch_size, len(self.pending)))]
            try:
                await self.storage.write_history(batch)
            except Exception as exc:  # keep the rows and try again next time
                if self._storage_ok:
                    log.error("cannot write history, will retry: %s", exc)
                self._storage_ok = False
                return
            for _ in batch:
                self.pending.popleft()
            self.written += len(batch)
            if not self._storage_ok:
                log.info("history writing has recovered")
            self._storage_ok = True
