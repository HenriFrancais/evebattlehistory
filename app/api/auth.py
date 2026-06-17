"""Caller identity + permission gating, derived from request.state.

The NV Tools proxy authenticates the user and the middleware copies the X-User-*
headers into request.state. Authorization (who may create a BR) is the app's
job, driven by the create_ranks / create_teams allowlist in config.toml.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import Request

from app.config import AppConfig, get_app_config


@dataclass(frozen=True)
class CurrentUser:
    user_name: str
    rank: str
    teams: list[str] = field(default_factory=list)
    main_character_id: str = ""

    @property
    def is_authenticated(self) -> bool:
        return bool(self.user_name)


def current_user(request: Request) -> CurrentUser:
    return CurrentUser(
        user_name=getattr(request.state, "user_name", ""),
        rank=getattr(request.state, "user_rank", ""),
        teams=list(getattr(request.state, "user_teams", [])),
        main_character_id=getattr(request.state, "user_main_character_id", ""),
    )


def can_create_br(user: CurrentUser, config: AppConfig | None = None) -> bool:
    """A user may create a BR if their rank is in create_ranks OR any of their
    teams is in create_teams (case-insensitive)."""
    cfg = config or get_app_config()
    if user.rank and user.rank.strip().lower() in {r.lower() for r in cfg.create_ranks}:
        return True
    user_teams = {t.lower() for t in user.teams}
    return bool(user_teams & {t.lower() for t in cfg.create_teams})
