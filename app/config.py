"""Runtime configuration: env-driven secrets/locations + a TOML app-config file.

Env vars (``Settings``) carry secrets and per-deploy values (NV_TOKEN, the
outbound NV_API_TOKEN, URL_PREFIX, DB/log locations, backup settings). The TOML
``AppConfig`` carries fleet data: who may create BRs and which entities are
"us" (friendly) for win/loss classification.
"""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseModel):
    """Loaded from config.toml (or config.local.toml). Defaults populated so a
    deployment without a config file still behaves sensibly."""

    # Who may CREATE a battle report. A user qualifies if their rank is in
    # create_ranks OR any of their teams is in create_teams (case-insensitive).
    create_ranks: list[str] = Field(default_factory=lambda: ["CEO", "Director", "High Command"])
    create_teams: list[str] = Field(default_factory=lambda: ["fc"])

    # The "us" entity set — used to label each fight side friendly/hostile and
    # compute BR-level win/loss + "we killed / we fielded" queries.
    our_alliance_ids: list[int] = Field(default_factory=list)
    our_corp_ids: list[int] = Field(default_factory=list)


class Settings(BaseSettings):
    """Env-driven secrets and locations."""

    # Inbound bearer: proves a request came from the NV Tools proxy.
    nv_token: str = "dev-token-change-me"
    # Path prefix the app is mounted under (e.g. "/br"). Empty = root (dev).
    url_prefix: str = ""

    # "real" calls the NV Tools portal APIs; "demo" reads committed fixtures.
    data_source: Literal["real", "demo"] = "real"
    demo_data_dir: Path = Path("./data_demo")

    # Outbound NV Tools portal API (roster). Separate bearer from nv_token.
    nv_api_url: str = "https://tools.novacancies.space/api"
    nv_api_token: str = ""

    # Persistence + uploads + caches.
    db_path: Path = Path("./var/db/nvbr.db")
    log_dir: Path = Path("./var/logs")
    esi_cache_dir: Path = Path("./var/esi")
    sde_dir: Path = Path("./var/sde")
    max_log_mb: int = 20

    # ESI / outbound etiquette.
    esi_user_agent: str = "nv-br (NV Tools; contact admin)"
    upstream_timeout_s: float = 30.0

    # Stale-while-revalidate TTL for the roster snapshot.
    roster_ttl_s: float = 1800.0

    # Backups (rclone → Google Drive). Empty remote disables.
    backup_rclone_remote: str = ""
    backup_keep: int = 30
    backup_hour: int = 3
    restore_on_start: bool = False

    config_path: Path = Path("./config.toml")
    config_local_path: Path = Path("./config.local.toml")

    # Dev-mode header overrides — only consulted when DEV_MODE=true.
    dev_mode: bool = False
    dev_user_rank: str = ""
    dev_user_teams: str = ""

    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("url_prefix", mode="after")
    @classmethod
    def _normalise_url_prefix(cls, v: str) -> str:
        v = v.strip()
        if not v:
            return ""
        if not v.startswith("/"):
            v = "/" + v
        return v.rstrip("/")


def load_app_config(path: Path) -> AppConfig:
    if not path.exists():
        return AppConfig()
    with path.open("rb") as f:
        data = tomllib.load(f)
    return AppConfig.model_validate(data)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def get_app_config() -> AppConfig:
    settings = get_settings()
    if settings.config_local_path.exists():
        return load_app_config(settings.config_local_path)
    return load_app_config(settings.config_path)
