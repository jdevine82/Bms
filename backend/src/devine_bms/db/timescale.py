"""TimescaleDB set-up for the history table (milestone 2 spec section 4)."""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Connection

from devine_bms.settings import get_settings

log = logging.getLogger(__name__)

RAW_RETENTION = "30 days"  # main spec 5.7
CHUNK_INTERVAL = "1 day"


class TimescaleMissing(RuntimeError):
    pass


def timescale_available(conn: Connection) -> bool:
    row = conn.execute(text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")).first()
    return row is not None


def setup_history_hypertable(conn: Connection) -> bool:
    """Make point_his a hypertable with a retention policy. Returns True if done.

    Sites must have TimescaleDB. On a development or test machine without it, set
    BMS_ALLOW_PLAIN_POSTGRES=true to keep a plain table (no retention policy).
    """
    if not timescale_available(conn):
        if get_settings().allow_plain_postgres:
            log.warning("TimescaleDB is not installed: point_his stays a plain table with no retention policy")
            return False
        raise TimescaleMissing(
            "TimescaleDB is not installed in this PostgreSQL server. Install it, or set "
            "BMS_ALLOW_PLAIN_POSTGRES=true on a development machine only."
        )
    conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
    conn.execute(text(
        f"SELECT create_hypertable('point_his', by_range('ts', INTERVAL '{CHUNK_INTERVAL}'), migrate_data => true)"
    ))
    conn.execute(text(f"SELECT add_retention_policy('point_his', INTERVAL '{RAW_RETENTION}')"))
    return True
