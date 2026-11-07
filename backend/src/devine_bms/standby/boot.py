"""Starting an HMI in standby (milestone 11, decision 029).

The Pi runs PostgreSQL in RAM as in standalone mode, so at boot its database is empty: the site
comes from the cache file on the config partition, or, the first time, from the server. The
runtime starts held: in standby the Pi polls nothing and sends nothing on any bus.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx

from devine_bms.standby.hmi import apply_cache, parse_ports
from devine_bms.standby.server import TOKEN_HEADER

log = logging.getLogger(__name__)
HELD_WHY = "standby: the site server drives the buses"


async def fetch_cache(settings) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{settings.server_url.rstrip('/')}/api/standby/cache",
                                 headers={TOKEN_HEADER: settings.standby_token})
    except httpx.HTTPError as exc:
        log.warning("the site server cannot be reached for the standby cache: %s", exc)
        return None
    if r.status_code != 200:
        log.error("the site server refused the standby cache (%s): is BMS_STANDBY_TOKEN the same on both?",
                  r.status_code)
        return None
    return r.json()


async def prepare_standby(sessions, settings, retry_s: float = 10.0) -> str:
    """Load the cached site into the database; the first time, wait for the server to give it."""
    from devine_bms.standalone.state import write_atomic

    if not settings.server_url or not settings.standby_token:
        raise RuntimeError("standby mode needs BMS_SERVER_URL and BMS_STANDBY_TOKEN")
    path = Path(settings.standby_cache)
    cache = None
    if path.is_file():
        try:
            cache = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.error("the standby cache %s cannot be read (%s)", path, exc)
    while cache is None:
        cache = await fetch_cache(settings)
        if cache is not None:
            await asyncio.to_thread(write_atomic, path, json.dumps(cache), settings.state_rw_cmd, settings.state_ro_cmd)
        else:
            log.info("no standby cache yet: asking the server again in %s s", retry_s)
            await asyncio.sleep(retry_s)
    name = await apply_cache(sessions, cache, parse_ports(settings.pi_ports))
    log.info("standby: site %s from the cache (revision %s)", name, cache.get("revision"))
    return name
