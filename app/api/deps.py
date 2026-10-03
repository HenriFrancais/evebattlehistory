from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.db.engine import get_sessionmaker
from app.db.models import BattleReport


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    settings = get_settings()
    session_maker = get_sessionmaker(settings)
    async with session_maker() as session:
        yield session


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    """Return the shared sessionmaker (for endpoints that open multiple sessions)."""
    return get_sessionmaker(get_settings())


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SessionMakerDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_maker)]


async def require_br(br_id: str, session: AsyncSession) -> None:
    """Raise 404 unless the battle report exists."""
    exists = (
        await session.execute(select(BattleReport.br_id).where(BattleReport.br_id == br_id))
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="Battle report not found")
