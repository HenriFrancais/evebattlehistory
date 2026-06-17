"""RosterSource protocol: where the /api/users payload comes from."""

from __future__ import annotations

from typing import Protocol

from app.roster.models import UsersPayload


class RosterSource(Protocol):
    name: str

    async def fetch_users(self) -> UsersPayload: ...
