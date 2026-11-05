"""Run the backend without the web API: poll and store history for the site in the database.

The config file is imported only when the database has no site yet (milestone 5).

    uv run python -m devine_bms.run

Used for development and the 24-hour soak run (milestone 2 spec 9). The FastAPI
app in part 3 starts the same Runtime.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from devine_bms.config.validate import ConfigError
from devine_bms.db.session import make_engine, make_sessionmaker
from devine_bms.logging_setup import setup_logging
from devine_bms.runtime import Runtime
from devine_bms.settings import get_settings
from devine_bms.setup.startup import StartupError, prepare_site
from devine_bms.storage.postgres import PostgresStorage

log = logging.getLogger("devine_bms.run")
STATUS_EVERY_S = 60


async def main() -> int:
    settings = get_settings()
    setup_logging(settings.log_level)
    engine = make_engine(settings.database_url)
    sessions = make_sessionmaker(engine)
    try:
        site_name = await prepare_site(sessions, settings)
    except (StartupError, ConfigError, OSError) as exc:
        log.error("cannot start: %s", exc)
        await engine.dispose()
        return 1

    runtime = Runtime(PostgresStorage(sessions), site_name)
    await runtime.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=STATUS_EVERY_S)
            except TimeoutError:
                counts: dict[str, int] = {}
                for live in runtime.cache.values.values():
                    counts[live.status.value] = counts.get(live.status.value, 0) + 1
                log.info(
                    "status: points %s, polls %d, failures %d, history rows written %d, pending %d",
                    counts, sum(p.polls for p in runtime.pollers), sum(p.failures for p in runtime.pollers),
                    runtime.history.written, len(runtime.history.pending),
                )
    finally:
        await runtime.stop()
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
