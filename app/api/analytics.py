"""FastAPI router for damage reconciliation and EWAR/logi analytics (Task 4.1)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.ewar import fight_ewar
from app.analytics.reconcile import fight_damage_reconcile
from app.api.deps import SessionDep
from app.api.schemas import (
    CapRowOut,
    CharacterReconcileRowOut,
    DpsPointOut,
    EwarRowOut,
    FightEwarOut,
    FightReconcileOut,
    LogiRowOut,
)
from app.db.models import BattleReport, BrFight

router = APIRouter()


async def _require_br_fight(br_id: str, fight_id: int, session: AsyncSession) -> None:
    """Raise 404 if the BR does not exist OR if fight_id is not linked to br_id."""
    br_exists = (
        await session.execute(select(BattleReport.br_id).where(BattleReport.br_id == br_id))
    ).scalar_one_or_none()
    if br_exists is None:
        raise HTTPException(status_code=404, detail="Battle report not found")

    fight_in_br = (
        await session.execute(
            select(BrFight.fight_id).where(
                BrFight.br_id == br_id,
                BrFight.fight_id == fight_id,
            )
        )
    ).scalar_one_or_none()
    if fight_in_br is None:
        raise HTTPException(status_code=404, detail="Fight not found in this battle report")


@router.get("/api/brs/{br_id}/fights/{fight_id}/reconcile")
async def get_fight_reconcile(
    br_id: str,
    fight_id: int,
    session: SessionDep,
) -> FightReconcileOut:
    """Return damage reconciliation for one fight in a battle report.

    Compares log-observed damage (what pilots actually applied) vs killmail
    attribution (only credited for ships that died).  The ``delta`` field
    surfaces the gap: damage applied to ships that survived the fight.

    404 if the battle report is unknown or the fight is not in this BR.
    """
    await _require_br_fight(br_id, fight_id, session)
    result = await fight_damage_reconcile(session, fight_id)

    return FightReconcileOut(
        rows=[
            CharacterReconcileRowOut(
                character_id=row.character_id,
                character_name=row.character_name,
                log_damage_out=row.log_damage_out,
                log_damage_in=row.log_damage_in,
                km_damage_attributed=row.km_damage_attributed,
                delta=row.delta,
            )
            for row in result.rows
        ],
        dps_series=[
            DpsPointOut(
                bucket_ts_epoch=pt.bucket_ts_epoch,
                sum_damage_out=pt.sum_damage_out,
            )
            for pt in result.dps_series
        ],
    )


@router.get("/api/brs/{br_id}/fights/{fight_id}/ewar")
async def get_fight_ewar(
    br_id: str,
    fight_id: int,
    session: SessionDep,
) -> FightEwarOut:
    """Return EWAR + logi effectiveness for one fight in a battle report.

    Surfaces tackle / electronic warfare, cap warfare, and remote repair activity
    from combat logs — information that killmails cannot provide.

    404 if the battle report is unknown or the fight is not in this BR.
    """
    await _require_br_fight(br_id, fight_id, session)
    result = await fight_ewar(session, fight_id)

    return FightEwarOut(
        ewar=[
            EwarRowOut(
                character_id=row.character_id,
                effect_type=row.effect_type,
                direction=row.direction,
                event_count=row.event_count,
                first_ts=row.first_ts,
                last_ts=row.last_ts,
            )
            for row in result.ewar
        ],
        cap=[
            CapRowOut(
                character_id=row.character_id,
                effect_type=row.effect_type,
                direction=row.direction,
                sum_amount=row.sum_amount,
                event_count=row.event_count,
                first_ts=row.first_ts,
                last_ts=row.last_ts,
            )
            for row in result.cap
        ],
        logi=[
            LogiRowOut(
                character_id=row.character_id,
                effect_type=row.effect_type,
                direction=row.direction,
                sum_amount=row.sum_amount,
                event_count=row.event_count,
                first_ts=row.first_ts,
                last_ts=row.last_ts,
            )
            for row in result.logi
        ],
    )
