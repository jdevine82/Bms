"""Starting the backend in standalone mode (milestone 11a, decision 028).

PostgreSQL runs in RAM, so at boot the database is empty: the state file is put back; without
one the config file is imported as in full mode; with neither, an empty site is made so the
setup can start in the web app.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import func, select

from devine_bms.config.model import POLL_INTERVAL_S, PollClass
from devine_bms.db import models as db
from devine_bms.standalone.state import StateKeeper, restore

log = logging.getLogger(__name__)

# The light profile: Slow points every 10 minutes (main spec 5.1.4), the log one sample a minute.
STANDALONE_INTERVALS = {**POLL_INTERVAL_S, PollClass.SLOW: 600.0}
LOG_EVERY_S = 60.0


async def prepare_standalone(sessions, settings) -> tuple[str, dict]:
    """The site to run, and the standalone settings from the state file."""
    from devine_bms.setup.startup import prepare_site

    path = Path(settings.state_file)
    async with sessions() as session:
        sites = await session.scalar(select(func.count()).select_from(db.Site))
    state = None
    if path.is_file():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.error("the state file %s cannot be read (%s); starting without it", path, exc)
    if not sites and state is not None:
        name = await restore(sessions, state)
        log.info("restored site %s from %s (saved %s)", name, path, state.get("savedAt"))
        return name, state.get("standalone") or {}
    if not sites and not Path(settings.config_path).is_file():
        async with sessions() as session, session.begin():
            session.add(db.Site(name="site", dis="New site"))
        log.info("no state file and no config file: made an empty site for the first-boot setup")
        return "site", {}
    return await prepare_site(sessions, settings), (state or {}).get("standalone") or {}


def make_keeper(sessions, site_name: str, settings, standalone: dict) -> StateKeeper:
    keeper = StateKeeper(sessions, site_name, Path(settings.state_file), settings.state_rw_cmd, settings.state_ro_cmd)
    keeper.standalone = dict(standalone)
    return keeper
