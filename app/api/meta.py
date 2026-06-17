"""Identity endpoint. Bearer-authenticated but not role-gated: the frontend
calls it first to learn whether the user may create BRs, so it can render the
create UI conditionally instead of surfacing raw 403s."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.api.auth import can_create_br, current_user

router = APIRouter()


class MeResponse(BaseModel):
    user_name: str
    user_rank: str
    user_teams: list[str]
    main_character_id: str
    can_create_br: bool


@router.get("/api/me")
async def me(request: Request) -> MeResponse:
    user = current_user(request)
    return MeResponse(
        user_name=user.user_name,
        user_rank=user.rank,
        user_teams=user.teams,
        main_character_id=user.main_character_id,
        can_create_br=can_create_br(user),
    )
