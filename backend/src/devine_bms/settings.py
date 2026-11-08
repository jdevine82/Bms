"""Backend settings from environment variables or a gitignored .env file (milestone 2 spec 6)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BMS_", env_file=".env", extra="ignore")

    # For example postgresql+asyncpg://bms:<password>@localhost:5432/bms
    database_url: str = "postgresql+asyncpg://bms@localhost:5432/bms"
    # Imported only when the database has no site yet (milestone 5: the database is the master copy).
    config_path: str = "../config/site.json"
    # Which site to run when the database holds more than one. Empty: the only site.
    site_name: str = ""
    listen_host: str = "127.0.0.1"
    listen_port: int = 8000
    # Proxies whose X-Forwarded-For and Tailscale identity headers are believed, besides loopback
    # (milestone 12a): on a site server the Proxmox host on the private bridge, 10.77.0.1.
    trusted_proxies: str = ""
    log_level: str = "INFO"
    # Built web app (web/dist) to serve at /. Leave empty when the Vite dev server is used.
    web_dist: str = ""
    # Sites always need TimescaleDB. Only set this on a development or test machine
    # without TimescaleDB: history then goes to a plain PostgreSQL table with no retention.
    allow_plain_postgres: bool = False

    # Standalone mode (milestone 11a): the Pi is the whole system; PostgreSQL runs in RAM and
    # the state file on the config partition keeps the site through a power cut.
    mode: str = "full"  # full, standalone, or standby (an HMI that can take over, milestone 11)
    state_file: str = "/var/lib/devine-bms/config/site-state.json"
    state_rw_cmd: str = ""  # for example: sudo mount -o remount,rw /var/lib/devine-bms/config
    state_ro_cmd: str = ""
    usb_path: str = "/run/devine-bms/usb"  # where the USB stick is mounted for an export
    usb_mount_cmd: str = ""  # for example: sudo /usr/local/sbin/bms-usb mount
    usb_umount_cmd: str = ""
    net_cmd: str = ""  # the Pi's network helper: show | dhcp | static ADDRESS/PREFIX GATEWAY DNS

    # HMI standby takeover (milestone 11). Both: the shared token (an environment variable only).
    standby_token: str = ""
    # Server: the HMIs that can take over, for example http://192.168.10.30:8000 (comma-separated).
    hmi_urls: str = ""
    # Pi (mode standby): the site server, this HMI's name, the cached setup, and the timings.
    server_url: str = ""
    hmi_name: str = "hmi"
    standby_cache: str = "/var/lib/devine-bms/config/standby-cache.json"
    heartbeat_s: float = 5.0  # both: how often the HMI asks the server, and the server the HMIs
    takeover_after_s: float = 30.0
    bus_quiet_s: float = 60.0
    failback_after_s: float = 300.0
    mstp_start_cmd: str = ""  # for example: sudo systemctl start bms-mstp@trunk1
    mstp_stop_cmd: str = ""
    pi_ports: str = ""  # the Pi's own adapters, for example: rs485_1=/dev/ttyBMS-rs485-1,trunk1=/dev/ttyBMS-trunk1

    # Device logins on the site's own Mosquitto (bms-app): the root helper, through sudo.
    mqtt_users_cmd: str = ""  # for example: sudo -n /usr/local/sbin/bms-mqtt-users

    # Updates (milestone 12b). Site server: the host's updater agent and its token (an environment
    # variable only), and where bms-app keeps releases (for pushing to the standby HMIs).
    updater_url: str = ""  # for example http://10.77.0.1:8701
    updater_token: str = ""
    release_dir: str = "/opt/devine-bms/releases"
    # Pi (standalone or standby): the updates folder on the config partition (the waiting release and
    # last-update.json, written by bms-pi-update as root) and the command that stages an upload.
    update_dir: str = "/var/lib/devine-bms/config/updates"
    pi_update_cmd: str = ""  # for example: sudo -n /usr/local/sbin/bms-pi-update stage


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
