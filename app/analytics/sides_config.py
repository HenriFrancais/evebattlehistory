"""Per-BR side classification: baseline blues + FC/HC manual overrides.

Classification precedence for a pilot (a killmail victim/attacker or a
log-identified participant), giving one of 'friendly' | 'hostile' | 'unassigned':
  0. A per-BR override on the CHARACTER (FC/HC choice; BrCharSide).
  1. A per-BR override on its alliance, then on its corp (FC/HC choice).
  2. Baseline friendly blues (the permanent NV alliances/corps from config) → friendly.
  3. Otherwise unassigned — FC/HC place it via the per-BR sides UI.

``SideResolver`` is the ONE implementation of that precedence; every reader
(headline outcome, per-fight losses, kill markers, timeline leaders, the BR list)
goes through it so they cannot disagree.

This is deliberately independent of the killmail 2-colouring, which is
unreliable in messy wormhole brawls (it can merge both fleets onto one side).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import (
    Alliance,
    BattleReport,
    BrCharSide,
    BrFight,
    BrSideOverride,
    Corporation,
    FightKill,
    Killmail,
    KillmailAttacker,
)
from app.fights.outcomes import classify_br_result

EntityKey = tuple[str, int]  # (entity_type, entity_id) where entity_type ∈ {"alliance","corp"}


def classify_entity(
    alliance_id: int | None,
    corp_id: int | None,
    *,
    baseline_alliances: set[int],
    baseline_corps: set[int],
    overrides: dict[EntityKey, str],
) -> str:
    """Return 'friendly' | 'hostile' | 'unassigned' for an entity. See module docstring."""
    if alliance_id is not None and ("alliance", alliance_id) in overrides:
        return overrides[("alliance", alliance_id)]
    if corp_id is not None and ("corp", corp_id) in overrides:
        return overrides[("corp", corp_id)]
    if (alliance_id is not None and alliance_id in baseline_alliances) or (
        corp_id is not None and corp_id in baseline_corps
    ):
        return "friendly"
    return "unassigned"


@dataclass(frozen=True)
class SideResolver:
    """Resolves a pilot's side for one battle report. See module docstring."""

    baseline_alliances: set[int] = field(default_factory=set)
    baseline_corps: set[int] = field(default_factory=set)
    overrides: dict[EntityKey, str] = field(default_factory=dict)
    char_sides: dict[int, str] = field(default_factory=dict)

    def entity(self, alliance_id: int | None, corp_id: int | None) -> str:
        return classify_entity(
            alliance_id,
            corp_id,
            baseline_alliances=self.baseline_alliances,
            baseline_corps=self.baseline_corps,
            overrides=self.overrides,
        )

    def character(
        self, character_id: int | None, alliance_id: int | None, corp_id: int | None
    ) -> str:
        if character_id is not None:
            forced = self.char_sides.get(character_id)
            if forced is not None:
                return forced
        return self.entity(alliance_id, corp_id)


async def load_char_sides(session: AsyncSession, br_id: str) -> dict[int, str]:
    """Load FC/HC per-character side overrides for *br_id*."""
    rows = (
        await session.execute(
            select(BrCharSide.character_id, BrCharSide.side).where(BrCharSide.br_id == br_id)
        )
    ).all()
    return {int(cid): side for cid, side in rows}


async def load_side_resolver(
    session: AsyncSession,
    br_id: str,
    *,
    baseline_alliances: set[int],
    baseline_corps: set[int],
) -> SideResolver:
    return SideResolver(
        baseline_alliances=set(baseline_alliances),
        baseline_corps=set(baseline_corps),
        overrides=await load_overrides(session, br_id),
        char_sides=await load_char_sides(session, br_id),
    )


async def load_overrides(session: AsyncSession, br_id: str) -> dict[EntityKey, str]:
    """Load FC/HC side overrides for *br_id* into a {(entity_type, id): side} dict."""
    rows = (
        await session.execute(
            select(BrSideOverride).where(BrSideOverride.br_id == br_id)
        )
    ).scalars()
    return {(r.entity_type, r.entity_id): r.side for r in rows}


async def fight_side_losses(
    session: AsyncSession,
    fight_ids: list[int],
    *,
    baseline_alliances: set[int],
    baseline_corps: set[int],
    overrides: dict[EntityKey, str],
    char_sides: dict[int, str] | None = None,
) -> dict[int, dict[str, dict]]:
    """Per fight, aggregate losses/ISK/pilots by classified side.

    Returns {fight_id: {side: {"losses": int, "isk_lost": float, "pilots": int,
    "isk_lost_to_friendly": float}}} where side ∈ {'friendly','hostile','unassigned'}.
    Losses + ISK come from victims; pilots counts distinct characters across
    victims and attackers. ``isk_lost_to_friendly`` is the part of ``isk_lost``
    from killmails that have at least one friendly attacker — i.e. kills we were
    actually on (a third party dying to someone else is not our kill).
    """
    resolver = SideResolver(
        baseline_alliances=baseline_alliances,
        baseline_corps=baseline_corps,
        overrides=overrides,
        char_sides=char_sides or {},
    )

    def _blank() -> dict:
        return {"losses": 0, "isk_lost": 0.0, "pilots": 0, "isk_lost_to_friendly": 0.0}

    out: dict[int, dict[str, dict]] = {fid: {} for fid in fight_ids}
    if not fight_ids:
        return out

    # fight_id ← killmail_id
    km_to_fight: dict[int, int] = {}
    for fid, kmid in (
        await session.execute(
            select(FightKill.fight_id, FightKill.killmail_id).where(
                FightKill.fight_id.in_(fight_ids)
            )
        )
    ).all():
        km_to_fight[kmid] = fid
    km_ids = list(km_to_fight)
    if not km_ids:
        return out

    pilots: dict[tuple[int, str], set[int]] = {}  # (fight_id, side) → {char_id}

    def _bump_pilot(fid: int, side: str, char_id: int | None) -> None:
        if char_id is None:
            return
        pilots.setdefault((fid, side), set()).add(char_id)

    # Attackers first → pilots, and which killmails a friendly pilot was on.
    friendly_on_km: set[int] = set()
    for kmid, alli, corp, char_id in (
        await session.execute(
            select(
                KillmailAttacker.killmail_id,
                KillmailAttacker.alliance_id,
                KillmailAttacker.corporation_id,
                KillmailAttacker.character_id,
            ).where(KillmailAttacker.killmail_id.in_(km_ids))
        )
    ).all():
        fid = km_to_fight[kmid]
        side = resolver.character(char_id, alli, corp)
        if side == "friendly":
            friendly_on_km.add(kmid)
        out[fid].setdefault(side, _blank())
        _bump_pilot(fid, side, char_id)

    # Victims → losses, isk, pilots.
    for kmid, alli, corp, char_id, value in (
        await session.execute(
            select(
                Killmail.killmail_id,
                Killmail.victim_alliance_id,
                Killmail.victim_corporation_id,
                Killmail.victim_character_id,
                Killmail.total_value,
            ).where(Killmail.killmail_id.in_(km_ids))
        )
    ).all():
        fid = km_to_fight[kmid]
        side = resolver.character(char_id, alli, corp)
        agg = out[fid].setdefault(side, _blank())
        agg["losses"] += 1
        agg["isk_lost"] += value or 0.0
        if kmid in friendly_on_km:
            agg["isk_lost_to_friendly"] += value or 0.0
        _bump_pilot(fid, side, char_id)

    for (fid, side), chars in pilots.items():
        out[fid][side]["pilots"] = len(chars)
    return out


async def recompute_br_outcome(
    session: AsyncSession,
    br_id: str,
    *,
    baseline_alliances: set[int],
    baseline_corps: set[int],
) -> dict[str, float | str | None]:
    """Re-derive a BR's headline stats from the current per-entity side allocation.

    Unlike the ingest-time killmail 2-colouring, this uses the same friendly /
    hostile / unassigned classification (baseline blues + FC/HC overrides) that
    drives the per-fight ISK figures, so the BR summary stays consistent with the
    Sides editor:

    - ``our_isk_lost``       = ISK lost by the **friendly** side ("us").
    - ``our_isk_destroyed``  = ISK lost by non-friendly pilots (hostile + unassigned)
      on killmails that carry **at least one friendly attacker** — what WE killed.
      A third party dying to someone else in the same system is not our kill.
    - ``isk_efficiency``     = destroyed / (destroyed + lost), or ``None`` when 0.
    - ``result``             = win / tie / loss via :func:`classify_br_result`.

    Writes the values onto the BattleReport row (caller commits) and returns them.
    """
    fight_ids = list(
        (
            await session.execute(select(BrFight.fight_id).where(BrFight.br_id == br_id))
        ).scalars()
    )
    overrides = await load_overrides(session, br_id)
    losses = await fight_side_losses(
        session,
        fight_ids,
        baseline_alliances=baseline_alliances,
        baseline_corps=baseline_corps,
        overrides=overrides,
        char_sides=await load_char_sides(session, br_id),
    )

    our_lost = 0.0
    our_destroyed = 0.0
    for side_map in losses.values():
        for side, agg in side_map.items():
            if side == "friendly":
                our_lost += agg["isk_lost"]
            else:
                our_destroyed += agg["isk_lost_to_friendly"]

    denom = our_destroyed + our_lost
    isk_eff: float | None = our_destroyed / denom if denom > 0 else None
    result = classify_br_result(our_destroyed, our_lost)

    br = (
        await session.execute(select(BattleReport).where(BattleReport.br_id == br_id))
    ).scalar_one_or_none()
    if br is not None:
        br.our_isk_destroyed = our_destroyed
        br.our_isk_lost = our_lost
        br.isk_efficiency = isk_eff
        br.result = result

    return {
        "our_isk_destroyed": our_destroyed,
        "our_isk_lost": our_lost,
        "isk_efficiency": isk_eff,
        "result": result,
    }


async def br_entities(
    session: AsyncSession,
    br_id: str,
    *,
    baseline_alliances: set[int],
    baseline_corps: set[int],
    overrides: dict[EntityKey, str],
    settings: Settings,
) -> list[dict]:
    """Enumerate the entities (alliances, and corps without an alliance) seen on
    the BR's killmails (victims + attackers) PLUS those of log-identified off-BR
    participants, each with its resolved name, current side classification, and
    whether it is overridden.

    Alliances are the primary unit; a corp is listed only when it has no
    alliance (so each pilot maps to exactly one assignable entity). Surfacing
    off-BR participants' entities lets FC/HC allocate them in the sides editor.
    """
    fk_ids = list(
        (
            await session.execute(
                select(FightKill.killmail_id)
                .join(BrFight, BrFight.fight_id == FightKill.fight_id)
                .where(BrFight.br_id == br_id)
            )
        ).scalars()
    )
    if not fk_ids:
        return []

    # (alliance_id, corp_id) pairs from victims and attackers.
    pairs: set[tuple[int | None, int | None]] = set()
    for vm_alli, vm_corp in (
        await session.execute(
            select(Killmail.victim_alliance_id, Killmail.victim_corporation_id).where(
                Killmail.killmail_id.in_(fk_ids)
            )
        )
    ).all():
        pairs.add((vm_alli, vm_corp))
    for at_alli, at_corp in (
        await session.execute(
            select(KillmailAttacker.alliance_id, KillmailAttacker.corporation_id).where(
                KillmailAttacker.killmail_id.in_(fk_ids)
            )
        )
    ).all():
        pairs.add((at_alli, at_corp))

    # Off-BR log-identified participants: surface their entities too, so FC/HC can
    # allocate any alliance/corp that exists only off the killboard. (Local import
    # avoids a sides_config ↔ composition ↔ offbr import cycle.)
    from app.fights.offbr_cache import get_offbr_cache

    for oc in await get_offbr_cache().get(session, settings, br_id):
        pairs.add((oc.alliance_id, oc.corporation_id))

    alliance_ids: set[int] = {a for a, _ in pairs if a is not None}
    # Corps that never appear under an alliance → assignable corp-only entities.
    corp_only: set[int] = {c for a, c in pairs if a is None and c is not None}

    # Resolve names.
    alli_names: dict[int, str | None] = {}
    if alliance_ids:
        for a in (
            await session.execute(select(Alliance).where(Alliance.alliance_id.in_(alliance_ids)))
        ).scalars():
            alli_names[a.alliance_id] = a.name
    corp_names: dict[int, str | None] = {}
    if corp_only:
        for c in (
            await session.execute(
                select(Corporation).where(Corporation.corporation_id.in_(corp_only))
            )
        ).scalars():
            corp_names[c.corporation_id] = c.name

    out: list[dict] = []
    for aid in sorted(alliance_ids):
        side = classify_entity(
            aid, None, baseline_alliances=baseline_alliances,
            baseline_corps=baseline_corps, overrides=overrides,
        )
        out.append({
            "entity_type": "alliance",
            "entity_id": aid,
            "name": alli_names.get(aid) or f"Alliance {aid}",
            "side": side,
            "overridden": ("alliance", aid) in overrides,
            "baseline": aid in baseline_alliances,
        })
    for cid in sorted(corp_only):
        side = classify_entity(
            None, cid, baseline_alliances=baseline_alliances,
            baseline_corps=baseline_corps, overrides=overrides,
        )
        out.append({
            "entity_type": "corp",
            "entity_id": cid,
            "name": corp_names.get(cid) or f"Corp {cid}",
            "side": side,
            "overridden": ("corp", cid) in overrides,
            "baseline": cid in baseline_corps,
        })
    # Friendly first, then by name, for a tidy UI.
    out.sort(key=lambda e: (e["side"] != "friendly", e["name"].lower()))
    return out
