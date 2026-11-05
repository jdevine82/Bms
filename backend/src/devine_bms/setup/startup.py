"""Which site the backend runs, at start-up (milestone 5 decision 1).

The database is the master copy. If it has the site, it is used as it is and the config
file is not read. Only when the database has no site yet is the config file imported,
once, so a fresh install can start from a file.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from devine_bms.config.sync import sync_config
from devine_bms.config.validate import load_config
from devine_bms.db import models as db
from devine_bms.settings import Settings

log = logging.getLogger(__name__)


class StartupError(RuntimeError):
    pass


async def prepare_site(sessions: async_sessionmaker, settings: Settings) -> str:
    async with sessions() as session:
        names = list((await session.scalars(select(db.Site.name).order_by(db.Site.name))).all())
    if settings.site_name:
        if settings.site_name in names:
            return settings.site_name
        if names:
            raise StartupError(f"BMS_SITE_NAME is '{settings.site_name}' but the database has {names}")
    elif len(names) == 1:
        return names[0]
    elif len(names) > 1:
        raise StartupError(f"the database has several sites {names}; set BMS_SITE_NAME to pick one")

    path = Path(settings.config_path)
    if not path.is_file():
        raise StartupError(f"the database has no site and there is no config file at {path} to import")
    config, warnings = load_config(path)
    for warning in warnings:
        log.warning("config: %s", warning)
    if settings.site_name and config.site.name != settings.site_name:
        raise StartupError(f"{path} is for site '{config.site.name}', not BMS_SITE_NAME '{settings.site_name}'")
    async with sessions() as session, session.begin():
        report = await sync_config(session, config)
        session.add(db.SetupChange(site_id=report.site_id, client="start-up", action="import",
                                   summary=f"imported {path.name} at first start", after=None))
    log.info("the database had no site: imported %s (site %s)", path, config.site.name)
    return config.site.name
