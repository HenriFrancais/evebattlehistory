"""Roster API endpoints for the frontend impersonation picker."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import get_settings
from app.roster.snapshot import get_roster_store

router = APIRouter()


class RosterUserOut(BaseModel):
    user_name: str
    main_character_id: int | None
    rank: str


@router.get("/api/roster/users")
async def get_roster_users() -> list[RosterUserOut]:
    """Return all roster users (lightweight) sorted by user_name.

    Used only by the frontend impersonation picker, so it exists in DEV_MODE
    alone: in production the full roster (with ranks) is not member-visible.
    """
    settings = get_settings()
    if not settings.dev_mode:
        raise HTTPException(status_code=404, detail="Not found")
    roster = await get_roster_store(settings).get()
    return sorted(
        [
            RosterUserOut(
                user_name=u.user_name,
                main_character_id=u.main_character_id,
                rank=u.rank,
            )
            for u in roster.users
        ],
        key=lambda u: u.user_name,
    )
