"""FastAPI router for per-BR side configuration (FC/HC manual overrides)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.sides_config import br_entities, load_overrides, recompute_br_outcome
from app.api.auth import can_create_br, current_user
from app.api.deps import SessionDep
from app.api.schemas import BrSidesOut, SideEntityOut, SideOverrideIn
from app.config import get_app_config, get_settings
from app.db.models import BattleReport, BrSideOverride

router = APIRouter()

_VALID_TYPES = {"alliance", "corp"}
_VALID_SIDES = {"friendly", "hostile", "unassigned"}


async def _require_br(br_id: str, session: AsyncSession) -> None:
    exists = (
        await session.execute(select(BattleReport.br_id).where(BattleReport.br_id == br_id))
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="Battle report not found")


async def _build_out(br_id: str, session: AsyncSession, can_edit: bool) -> BrSidesOut:
    cfg = get_app_config()
    overrides = await load_overrides(session, br_id)
    entities = await br_entities(
        session,
        br_id,
        baseline_alliances=set(cfg.our_alliance_ids),
        baseline_corps=set(cfg.our_corp_ids),
        overrides=overrides,
        settings=get_settings(),
    )
    return BrSidesOut(
        entities=[SideEntityOut(**e) for e in entities],
        can_edit=can_edit,
    )


@router.get("/api/brs/{br_id}/sides")
async def get_sides(br_id: str, session: SessionDep, request: Request) -> BrSidesOut:
    """List the entities in a BR with their current side classification."""
    await _require_br(br_id, session)
    user = current_user(request)
    return await _build_out(br_id, session, can_edit=can_create_br(user))


@router.put("/api/brs/{br_id}/sides")
async def set_side(
    br_id: str, body: SideOverrideIn, session: SessionDep, request: Request
) -> BrSidesOut:
    """Set or clear a per-BR side override for one entity. FC/HC only.

    ``side`` = 'friendly'/'hostile' to set; ``null`` to clear (revert to default).
    """
    await _require_br(br_id, session)
    user = current_user(request)
    if not can_create_br(user):
        raise HTTPException(status_code=403, detail="Forbidden")
    if body.entity_type not in _VALID_TYPES:
        raise HTTPException(status_code=400, detail="invalid entity_type")
    if body.side is not None and body.side not in _VALID_SIDES:
        raise HTTPException(status_code=400, detail="invalid side")

    # Replace any existing override for this entity.
    await session.execute(
        delete(BrSideOverride).where(
            BrSideOverride.br_id == br_id,
            BrSideOverride.entity_type == body.entity_type,
            BrSideOverride.entity_id == body.entity_id,
        )
    )
    if body.side in _VALID_SIDES:
        session.add(
            BrSideOverride(
                br_id=br_id,
                entity_type=body.entity_type,
                entity_id=body.entity_id,
                side=body.side,
            )
        )
    await session.flush()

    # Keep the BR headline stats (result / efficiency / ISK) in step with the
    # new side allocation, so the timeline + summary reflect the change.
    cfg = get_app_config()
    await recompute_br_outcome(
        session,
        br_id,
        baseline_alliances=set(cfg.our_alliance_ids),
        baseline_corps=set(cfg.our_corp_ids),
    )
    await session.commit()
    return await _build_out(br_id, session, can_edit=True)
