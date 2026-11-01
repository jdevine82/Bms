"""Backend settings from environment variables or a gitignored .env file (milestone 2 spec 6)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BMS_", env_file=".env", extra="ignore")

    # For example postgresql+asyncpg://bms:<password>@localhost:5432/bms
    database_url: str = "postgresql+asyncpg://bms@localhost:5432/bms"
    config_path: str = "../config/site.json"
    listen_host: str = "127.0.0.1"
    listen_port: int = 8000
    log_level: str = "INFO"
    # Sites always need TimescaleDB. Only set this on a development or test machine
    # without TimescaleDB: history then goes to a plain PostgreSQL table with no retention.
    allow_plain_postgres: bool = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
