"""Per-BR enrichment for the battle-report timeline list.

Adds the columns the timeline table needs but the BattleReport row doesn't store:

- ``our_name`` / ``opponent_name`` — the friendly side ("us") and the single
  largest non-friendly side ("them"), named after the alliance (or alliance-less
  corp) fielding the most distinct pilots on that side.
- ``friendly_pilots`` / ``enemy_pilots`` — distinct pilot counts per side.
- ``you_present`` and the two log-coverage fractions: the viewer's own characters
  (``your_logged`` / ``your_present``) and the whole NV roster present in the BR
  (``roster_logged`` / ``roster_present``).

Side classification reuses :mod:`app.analytics.sides_config` (baseline blues +
FC/HC overrides), so it matches the rest of the app.

Everything is computed with a fixed handful of **batched** queries across all
requested BRs (not per-BR/per-fight), so the overview stays cheap as the number
of battle reports and fights grows.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.sides_config import classify_entity
from app.config import Settings
from app.db.models import (
    Alliance,
    BrCharSide,
    BrFight,
    BrSideOverride,
    Character,
    Corporation,
    Fight,
    FightKill,
    Killmail,
    KillmailAttacker,
    LogEvent,
    SolarSystem,
)
from app.roster.snapshot import get_roster_store

# Entity key for naming a side: ('alliance', id) or ('corp', id).
_EntityKey = tuple[str, int]


@dataclass
class BrRowExtra:
    """Extra timeline columns for one BR."""

    systems: list[str] = field(default_factory=list)
    #: Solar-system ids parallel to `systems` (same order/length).
    system_ids: list[int] = field(default_factory=list)
    our_name: str | None = None
    opponent_name: str | None = None
    friendly_pilots: int = 0
    enemy_pilots: int = 0
    you_present: bool = False
    your_present: int = 0
    your_logged: int = 0
    roster_present: int = 0
    roster_logged: int = 0


def _entity_key(alliance_id: int | None, corp_id: int | None) -> _EntityKey | None:
    if alliance_id is not None:
        return ("alliance", alliance_id)
    if corp_id is not None:
        return ("corp", corp_id)
    return None


async def _resolve_names(
    session: AsyncSession, keys: list[_EntityKey]
) -> dict[_EntityKey, str | None]:
    """Batch-resolve alliance/corp entity keys to names."""
    alli_ids = [kid for (kind, kid) in keys if kind == "alliance"]
    corp_ids = [kid for (kind, kid) in keys if kind == "corp"]
    out: dict[_EntityKey, str | None] = {}
    if alli_ids:
        for a in (
            await session.execute(select(Alliance).where(Alliance.alliance_id.in_(alli_ids)))
        ).scalars():
            out[("alliance", a.alliance_id)] = a.name
    if corp_ids:
        for c in (
            await session.execute(
                select(Corporation).where(Corporation.corporation_id.in_(corp_ids))
            )
        ).scalars():
            out[("corp", c.corporation_id)] = c.name
    return out


async def enrich_br_rows(
    session: AsyncSession,
    settings: Settings,
    br_ids: list[str],
    *,
    user_name: str,
    baseline_alliances: set[int],
    baseline_corps: set[int],
) -> dict[str, BrRowExtra]:
    """Compute timeline extras for each BR id, keyed by br_id.

    Uses a constant number of batched queries for the whole ``br_ids`` set rather
    than looping per BR / per fight.
    """
    if not br_ids:
        return {}

    roster = await get_roster_store(settings).get()
    char_to_user = roster.char_to_user
    your_char_ids = {cid for cid, un in char_to_user.items() if un == user_name}

    # --- 1. BR ↔ fight maps (one query) ---
    fight_to_br: dict[int, str] = {}
    for br, fid in (
        await session.execute(
            select(BrFight.br_id, BrFight.fight_id).where(BrFight.br_id.in_(br_ids))
        )
    ).all():
        fight_to_br[fid] = br
    all_fight_ids = list(fight_to_br)

    # Per-BR accumulators.
    km_chars: dict[str, set[int]] = {b: set() for b in br_ids}
    log_chars: dict[str, set[int]] = {b: set() for b in br_ids}
    # char_id → (alliance_id, corp_id), first occurrence wins (victims then attackers).
    char_entity: dict[str, dict[int, tuple[int | None, int | None]]] = {b: {} for b in br_ids}

    if all_fight_ids:
        # --- 2. killmail → fight (one query) ---
        km_to_fight: dict[int, int] = {}
        for fid, kmid in (
            await session.execute(
                select(FightKill.fight_id, FightKill.killmail_id).where(
                    FightKill.fight_id.in_(all_fight_ids)
                )
            )
        ).all():
            km_to_fight[kmid] = fid
        km_ids = list(km_to_fight)

        if km_ids:
            # --- 3. victims, then 4. attackers (two queries) ---
            for kmid, alli, corp, char in (
                await session.execute(
                    select(
                        Killmail.killmail_id,
                        Killmail.victim_alliance_id,
                        Killmail.victim_corporation_id,
                        Killmail.victim_character_id,
                    ).where(Killmail.killmail_id.in_(km_ids))
                )
            ).all():
                if char is None:
                    continue
                br = fight_to_br[km_to_fight[kmid]]
                km_chars[br].add(char)
                char_entity[br].setdefault(char, (alli, corp))
            for kmid, alli, corp, char in (
                await session.execute(
                    select(
                        KillmailAttacker.killmail_id,
                        KillmailAttacker.alliance_id,
                        KillmailAttacker.corporation_id,
                        KillmailAttacker.character_id,
                    ).where(KillmailAttacker.killmail_id.in_(km_ids))
                )
            ).all():
                if char is None:
                    continue
                br = fight_to_br[km_to_fight[kmid]]
                km_chars[br].add(char)
                char_entity[br].setdefault(char, (alli, corp))

        # --- 5. logged characters (one query) ---
        for char, fid in (
            await session.execute(
                select(LogEvent.character_id, LogEvent.fight_id)
                .where(LogEvent.fight_id.in_(all_fight_ids), LogEvent.character_id.is_not(None))
                .distinct()
            )
        ).all():
            br = fight_to_br.get(fid)
            if br is not None and char is not None:
                log_chars[br].add(int(char))

    # --- 6. side overrides (one query) ---
    overrides_by_br: dict[str, dict[_EntityKey, str]] = {b: {} for b in br_ids}
    for br, etype, eid, side in (
        await session.execute(
            select(
                BrSideOverride.br_id,
                BrSideOverride.entity_type,
                BrSideOverride.entity_id,
                BrSideOverride.side,
            ).where(BrSideOverride.br_id.in_(br_ids))
        )
    ).all():
        overrides_by_br.setdefault(br, {})[(etype, eid)] = side

    # --- 6b. per-character side overrides (FC/HC), and affiliations for log-only
    # characters not on any killmail (so off-BR participants are classified too). ---
    char_side_by_br: dict[str, dict[int, str]] = {b: {} for b in br_ids}
    for br, cid, side in (
        await session.execute(
            select(BrCharSide.br_id, BrCharSide.character_id, BrCharSide.side).where(
                BrCharSide.br_id.in_(br_ids)
            )
        )
    ).all():
        char_side_by_br.setdefault(br, {})[int(cid)] = side

    log_only_ids = {c for b in br_ids for c in log_chars[b] if c not in char_entity[b]}
    log_char_aff: dict[int, tuple[int | None, int | None]] = {}
    if log_only_ids:
        for cid, alli, corp in (
            await session.execute(
                select(
                    Character.character_id, Character.alliance_id, Character.corporation_id
                ).where(Character.character_id.in_(log_only_ids))
            )
        ).all():
            log_char_aff[int(cid)] = (alli, corp)

    # --- 7. systems per BR, in fight order (one query) ---
    # systems_by_br and system_ids_by_br are kept parallel (same dedup, order).
    systems_by_br: dict[str, list[str]] = {b: [] for b in br_ids}
    system_ids_by_br: dict[str, list[int]] = {b: [] for b in br_ids}
    sys_seen: dict[str, set[int]] = {b: set() for b in br_ids}
    if all_fight_ids:
        for br, sid, name in (
            await session.execute(
                select(BrFight.br_id, SolarSystem.system_id, SolarSystem.name)
                .join(Fight, Fight.fight_id == BrFight.fight_id)
                .join(SolarSystem, SolarSystem.system_id == Fight.system_id)
                .where(BrFight.br_id.in_(br_ids))
                .order_by(BrFight.seq)
            )
        ).all():
            if sid not in sys_seen[br]:
                sys_seen[br].add(sid)
                systems_by_br[br].append(name or f"System {sid}")
                system_ids_by_br[br].append(sid)

    # --- 8. classify pilots per BR; collect entity keys for one name lookup ---
    per_br_sides: dict[str, tuple[_EntityKey | None, _EntityKey | None, int, int]] = {}
    all_keys: set[_EntityKey] = set()
    for br in br_ids:
        friendly_pilots = enemy_pilots = 0
        friendly_groups: dict[_EntityKey, int] = {}
        enemy_groups: dict[_EntityKey, int] = {}
        char_sides = char_side_by_br.get(br, {})
        # Count every participant — killmail + log-derived — once, by character.
        for char in km_chars[br] | log_chars[br]:
            alli, corp = char_entity[br].get(char) or log_char_aff.get(char, (None, None))
            # A per-character FC/HC override wins over entity classification.
            side = char_sides.get(char) or classify_entity(
                alli, corp, baseline_alliances=baseline_alliances,
                baseline_corps=baseline_corps, overrides=overrides_by_br.get(br, {}),
            )
            key = _entity_key(alli, corp)
            if side == "friendly":
                friendly_pilots += 1
                if key is not None:
                    friendly_groups[key] = friendly_groups.get(key, 0) + 1
            else:
                enemy_pilots += 1
                if key is not None:
                    enemy_groups[key] = enemy_groups.get(key, 0) + 1
        our_key = max(friendly_groups, key=lambda k: friendly_groups[k], default=None)
        opp_key = max(enemy_groups, key=lambda k: enemy_groups[k], default=None)
        if our_key is not None:
            all_keys.add(our_key)
        if opp_key is not None:
            all_keys.add(opp_key)
        per_br_sides[br] = (our_key, opp_key, friendly_pilots, enemy_pilots)

    names = await _resolve_names(session, list(all_keys))

    # --- 9. assemble per-BR rows ---
    out: dict[str, BrRowExtra] = {}
    for br in br_ids:
        all_chars = km_chars[br] | log_chars[br]
        roster_present = sum(1 for c in all_chars if char_to_user.get(c) is not None)
        roster_logged = sum(1 for c in log_chars[br] if char_to_user.get(c) is not None)
        your_present = sum(1 for c in all_chars if c in your_char_ids)
        your_logged = sum(1 for c in log_chars[br] if c in your_char_ids)
        our_key, opp_key, friendly_pilots, enemy_pilots = per_br_sides[br]
        out[br] = BrRowExtra(
            systems=systems_by_br[br],
            system_ids=system_ids_by_br[br],
            our_name=names.get(our_key) if our_key is not None else None,
            opponent_name=names.get(opp_key) if opp_key is not None else None,
            friendly_pilots=friendly_pilots,
            enemy_pilots=enemy_pilots,
            you_present=your_present > 0,
            your_present=your_present,
            your_logged=your_logged,
            roster_present=roster_present,
            roster_logged=roster_logged,
        )
    return out
