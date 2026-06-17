"""Typed models for the NV Tools /api/users roster payload."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RosterCharacter(BaseModel):
    character_id: int
    character_name: str


class RosterUser(BaseModel):
    user_name: str
    main_character_id: int | None = None
    characters: list[RosterCharacter] = Field(default_factory=list)
    discord_id: str | None = None
    rank: str = ""
    teams: list[str] = Field(default_factory=list)
    allowed_apps: list[str] = Field(default_factory=list)


class UsersPayload(BaseModel):
    """Wraps the bare JSON array returned by GET /api/users."""

    users: list[RosterUser]

    @classmethod
    def from_api(cls, data: object) -> UsersPayload:
        if isinstance(data, list):
            return cls(users=[RosterUser.model_validate(u) for u in data])
        # Tolerate an already-wrapped shape.
        return cls.model_validate(data)
