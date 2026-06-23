"""Fleet composition: per-side ship counts, per-pilot roster, and (for elevated
callers) char→user grouping. Killmail-derived: a pilot is any character seen as
a Killmail victim or a KillmailAttacker across the BR's fights.

Capsules (type_id 670) are excluded everywhere. Each character maps to a *hull
set* — the distinct non-capsule ships they flew as victim or attacker:
  - a character with >1 hull is a reship (every hull row flagged reship=True);
  - a capsule-only (podded) character becomes one hull-less "Unknown" row;
  - side: classify_entity(alliance_id, corp_id, baseline, overrides), victim wins.
Consistent with the kill-marker classification on the fleet timeline.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.fleet import _resolve_char_names
from app.analytics.sides_config import EntityKey, classify_entity
from app.analytics.weapon_roles import WeaponTypeInfo, weapon_role
from app.config import Settings
from app.db.models import (
    BrCharShip,
    BrFight,
    FightKill,
    InventoryType,
    Killmail,
    KillmailAttacker,
    LogEvent,
)

_SIDE_ORDER = ("friendly", "hostile", "unassigned")

CAPSULE_TYPE_ID = 670


@dataclass
class WeaponEffect:
    type_id: int
    name: str
    role: str


@dataclass
class CompositionPilot:
    character_id: int
    character_name: str
    ship_type_id: int | None
    ship_name: str
    lost: bool
    reship: bool
    user_name: str | None
    killmail_id: int | None
    weapons: list[WeaponEffect]
    #: Total damage this character dealt across all of the BR's killmails (attacker rows).
    damage_done: int = 0
    #: Distinct killmails this character is involved with as an attacker.
    kill_count: int = 0
    #: Total HP this character repaired onto others (logi "out" log events across the BR).
    reps_out: float = 0.0
    #: True when this character has uploaded gamelogs associated with the BR's fights.
    has_logs: bool = False
    #: True when this pilot is NOT on any killmail and was identified from logs.
    from_logs: bool = False


@dataclass
class CompositionShip:
    ship_type_id: int
    ship_name: str
    count: int
    #: Up to 5 most common modules among pilots flying this hull, most-common first.
    top_modules: list[WeaponEffect] = field(default_factory=list)


@dataclass
class CompositionSide:
    side_kind: str
    pilot_count: int
    ships: list[CompositionShip]
    pilots: list[CompositionPilot]


@dataclass
class CompositionResult:
    sides: list[CompositionSide]


@dataclass
class _Acc:
    side: str
    hulls: dict[int, tuple[bool, int | None]]  # ship_type_id → (lost?, killmail_id)
    podded: bool            # appeared only in a capsule
    weapons_by_hull: dict[int, set[int]]  # ship_type_id → weapon_type_ids used from that hull


_REP_TYPES = ("rep_armor", "rep_shield")


async def _reps_applied_by_char(
    session: AsyncSession,
    fight_ids: list[int],
    settings: Settings,
    char_names: dict[int, str],
) -> dict[int, float]:
    """Total HP each character remote-repaired onto others across *fight_ids*.

    A single physical rep tick can be logged twice — once by the repper
    (direction="out") and once by the recipient (direction="in", other_name=repper).
    We use *all* of that information without double counting:

      * every "out" tick is the repper's own authoritative record — counted in full;
      * an "in" tick is added only when no matching "out" tick exists (i.e. the repper
        never logged that tick), so reps from non-uploading logi are still captured.

    Ticks are matched on (repper_name, recipient_name, effect_type, ts, amount). EVE
    stamps both clients with the same server-second timestamp, so the same physical tick
    lines up across the two logs; a Counter handles ticks that repeat within a second.
    """
    reps: dict[int, float] = {}
    if not fight_ids:
        return reps

    rows = (
        await session.execute(
            select(
                LogEvent.character_id,
                LogEvent.direction,
                LogEvent.other_name,
                LogEvent.effect_type,
                LogEvent.amount,
                LogEvent.ts,
            ).where(
                LogEvent.fight_id.in_(fight_ids),
                LogEvent.effect_type.in_(_REP_TYPES),
                LogEvent.direction.in_(["out", "in"]),
                LogEvent.character_id.is_not(None),
                LogEvent.amount.is_not(None),
            )
        )
    ).all()
    if not rows:
        return reps

    # id→name for every log owner (superset of the killmail-derived char_names), plus the
    # inverse over displayable pilots for attributing recipient-only ("in") reps.
    id_to_name: dict[int, str] = dict(char_names)
    missing = {int(r[0]) for r in rows} - id_to_name.keys()
    if missing:
        id_to_name.update(await _resolve_char_names(session, settings, missing))
    name_to_id = {name: cid for cid, name in char_names.items()}

    # Pass 1: count every "out" tick for displayable reppers; index all out ticks.
    out_ticks: Counter[tuple] = Counter()
    for cid, direction, other_name, et, amount, ts in rows:
        if direction != "out":
            continue
        cid = int(cid)
        amt = float(amount)
        if cid in char_names:
            reps[cid] = reps.get(cid, 0.0) + amt
        repper_name = id_to_name.get(cid)
        if repper_name is not None and other_name is not None:
            out_ticks[(repper_name, other_name, et, ts, amt)] += 1

    # Pass 2: add "in" ticks with no matching "out" tick (repper never logged them).
    for cid, direction, other_name, et, amount, ts in rows:
        if direction != "in" or other_name is None:
            continue
        recipient_name = id_to_name.get(int(cid))
        if recipient_name is None:
            continue
        amt = float(amount)
        key = (other_name, recipient_name, et, ts, amt)
        if out_ticks.get(key, 0) > 0:
            out_ticks[key] -= 1  # consume the authoritative duplicate
            continue
        repper_id = name_to_id.get(other_name)
        if repper_id is not None:
            reps[repper_id] = reps.get(repper_id, 0.0) + amt

    return reps


async def fleet_composition(
    session: AsyncSession,
    br_id: str,
    *,
    baseline_alliances: set[int],
    baseline_corps: set[int],
    overrides: dict[EntityKey, str],
    settings: Settings,
    char_to_user: dict[int, str] | None,
) -> CompositionResult:
    """Build per-side composition for *br_id*. See module docstring."""
    km_ids = list(
        (
            await session.execute(
                select(FightKill.killmail_id)
                .join(BrFight, BrFight.fight_id == FightKill.fight_id)
                .where(BrFight.br_id == br_id)
            )
        ).scalars()
    )
    if not km_ids:
        return CompositionResult(sides=[])

    def _side(alli: int | None, corp: int | None) -> str:
        return classify_entity(
            alli, corp, baseline_alliances=baseline_alliances,
            baseline_corps=baseline_corps, overrides=overrides,
        )

    acc: dict[int, _Acc] = {}

    def _ensure(char_id: int, side: str) -> _Acc:
        a = acc.get(char_id)
        if a is None:
            a = _Acc(side=side, hulls={}, podded=False, weapons_by_hull={})
            acc[char_id] = a
        return a

    # Victims: authoritative side + a lost hull (capsules → podded, not a hull).
    for km_id, char_id, ship_id, alli, corp in (
        await session.execute(
            select(
                Killmail.killmail_id,
                Killmail.victim_character_id,
                Killmail.victim_ship_type_id,
                Killmail.victim_alliance_id,
                Killmail.victim_corporation_id,
            ).where(Killmail.killmail_id.in_(km_ids))
        )
    ).all():
        if char_id is None:
            continue
        a = _ensure(char_id, _side(alli, corp))
        a.side = _side(alli, corp)  # victim entity wins for side
        if ship_id is not None and ship_id != CAPSULE_TYPE_ID:
            a.hulls[ship_id] = (True, km_id)
        elif ship_id == CAPSULE_TYPE_ID:
            a.podded = True

    # Attackers: side if unseen, plus any non-capsule hull they flew + weapon id.
    for char_id, ship_id, weapon_id, alli, corp in (
        await session.execute(
            select(
                KillmailAttacker.character_id,
                KillmailAttacker.ship_type_id,
                KillmailAttacker.weapon_type_id,
                KillmailAttacker.alliance_id,
                KillmailAttacker.corporation_id,
            ).where(KillmailAttacker.killmail_id.in_(km_ids))
        )
    ).all():
        if char_id is None:
            continue
        a = acc.get(char_id) or _ensure(char_id, _side(alli, corp))
        if ship_id is not None and ship_id != CAPSULE_TYPE_ID:
            a.hulls.setdefault(ship_id, (False, None))
            # A weapon belongs to the hull on its own attacker row, so a reship's
            # modules stay with the ship they were used from. Rows with no/capsule
            # hull can't be attributed to a hull, so their weapons are dropped.
            if weapon_id is not None and weapon_id != CAPSULE_TYPE_ID:
                a.weapons_by_hull.setdefault(ship_id, set()).add(weapon_id)

    # Off-BR log-identified participants: fold them in so they appear on their
    # classified side. Side via classify_entity; ship via FC/HC override →
    # log-detected → Unknown. Marked from_logs; counted in pilot_count, and (when a
    # ship is known) in that ship's tally. (Local import avoids an import cycle.)
    from app.fights.offbr_participants import offbr_log_characters

    ship_overrides: dict[int, int] = {
        int(cid): int(sid)
        for cid, sid in (
            await session.execute(
                select(BrCharShip.character_id, BrCharShip.ship_type_id).where(
                    BrCharShip.br_id == br_id
                )
            )
        ).all()
    }
    from_logs_ids: set[int] = set()
    for oc in await offbr_log_characters(session, settings, br_id):
        if oc.character_id in acc:
            continue
        from_logs_ids.add(oc.character_id)
        a = _ensure(oc.character_id, _side(oc.alliance_id, oc.corporation_id))
        ship = ship_overrides.get(oc.character_id) or oc.detected_ship_type_id
        if ship is not None:
            a.hulls[ship] = (False, None)

    char_names = await _resolve_char_names(session, settings, set(acc))
    ship_ids = {sid for a in acc.values() for sid in a.hulls}
    weapon_ids_all = {
        wid for a in acc.values() for wids in a.weapons_by_hull.values() for wid in wids
    }
    all_type_ids = ship_ids | weapon_ids_all
    ship_names: dict[int, str] = {}
    inv_by_id: dict[int, InventoryType] = {}
    if all_type_ids:
        for inv in (
            await session.execute(
                select(InventoryType).where(InventoryType.type_id.in_(all_type_ids))
            )
        ).scalars():
            inv_by_id[inv.type_id] = inv
            if inv.type_id in ship_ids:
                ship_names[inv.type_id] = inv.name

    # Per-character battle stats: damage dealt + count of distinct killmails the
    # character is an attacker on, across the whole BR.
    dmg_by_char: dict[int, int] = {}
    kc_by_char: dict[int, int] = {}
    for char_id, dmg, kc in (
        await session.execute(
            select(
                KillmailAttacker.character_id,
                func.sum(KillmailAttacker.damage_done),
                func.count(func.distinct(KillmailAttacker.killmail_id)),
            )
            .where(
                KillmailAttacker.killmail_id.in_(km_ids),
                KillmailAttacker.character_id.is_not(None),
            )
            .group_by(KillmailAttacker.character_id)
        )
    ).all():
        if char_id is None:
            continue
        dmg_by_char[int(char_id)] = int(dmg or 0)
        kc_by_char[int(char_id)] = int(kc or 0)

    # Reps applied (logistics output), deduped across the two logs that can record the
    # same tick — see _reps_applied_by_char.
    fight_ids = list(
        (
            await session.execute(select(BrFight.fight_id).where(BrFight.br_id == br_id))
        ).scalars()
    )
    reps_by_char = await _reps_applied_by_char(session, fight_ids, settings, char_names)

    # Characters who uploaded gamelogs associated with this BR (log owners).
    log_char_ids: set[int] = set()
    if fight_ids:
        log_char_ids = {
            int(cid)
            for cid in (
                await session.execute(
                    select(LogEvent.character_id)
                    .where(
                        LogEvent.fight_id.in_(fight_ids),
                        LogEvent.character_id.is_not(None),
                    )
                    .distinct()
                )
            ).scalars()
            if cid is not None
        }

    def _weapons_for(wids: set[int], hull_id: int) -> list[WeaponEffect]:
        out: list[WeaponEffect] = []
        for wid in wids:
            # The hull is sometimes logged as a weapon; exclude it from the
            # per-pilot module list (mirrors the summary top_modules filter).
            if wid == hull_id:
                continue
            winv = inv_by_id.get(wid)
            if winv is None:
                continue
            info = WeaponTypeInfo(
                type_id=wid, name=winv.name,
                group_name=winv.group_name, category_id=winv.category_id,
            )
            wr = weapon_role(info)
            out.append(WeaponEffect(type_id=wid, name=winv.name, role=wr.role))
        return out

    by_side: dict[str, list[CompositionPilot]] = {}
    for char_id, a in acc.items():
        name = char_names.get(char_id) or f"Char {char_id}"
        user = char_to_user.get(char_id) if char_to_user else None
        is_reship = len(a.hulls) > 1
        if a.hulls:
            for sid, (lost, km_id) in a.hulls.items():
                hull_weapons = _weapons_for(a.weapons_by_hull.get(sid, set()), sid)
                by_side.setdefault(a.side, []).append(
                    CompositionPilot(character_id=char_id, character_name=name, ship_type_id=sid,
                                     ship_name=ship_names.get(sid, "Unknown"), lost=lost,
                                     reship=is_reship, user_name=user, killmail_id=km_id,
                                     weapons=hull_weapons,
                                     damage_done=dmg_by_char.get(char_id, 0),
                                     kill_count=kc_by_char.get(char_id, 0),
                                     reps_out=reps_by_char.get(char_id, 0.0),
                                     has_logs=char_id in log_char_ids,
                                     from_logs=char_id in from_logs_ids)
                )
        else:
            # Capsule-only / no hull recorded: no hull to attribute modules to.
            by_side.setdefault(a.side, []).append(
                CompositionPilot(character_id=char_id, character_name=name, ship_type_id=None,
                                 ship_name="Unknown", lost=a.podded, reship=False,
                                 user_name=user, killmail_id=None, weapons=[],
                                 damage_done=dmg_by_char.get(char_id, 0),
                                 kill_count=kc_by_char.get(char_id, 0),
                                 reps_out=reps_by_char.get(char_id, 0.0),
                                 has_logs=char_id in log_char_ids,
                                 from_logs=char_id in from_logs_ids)
            )

    sides: list[CompositionSide] = []
    for side_kind in _SIDE_ORDER:
        plist = by_side.get(side_kind)
        if not plist:
            continue
        counts: Counter[int] = Counter()
        # Per ship_type_id: how many pilots flying it carry each module type_id.
        mod_counts: dict[int, Counter[int]] = {}
        mod_effect: dict[int, WeaponEffect] = {}
        for pilot in plist:
            if pilot.ship_type_id is None:
                continue
            counts[pilot.ship_type_id] += 1
            per_hull = mod_counts.setdefault(pilot.ship_type_id, Counter())
            for w in pilot.weapons:
                if w.type_id == pilot.ship_type_id:
                    continue  # the hull itself is logged as a weapon sometimes — not a module
                per_hull[w.type_id] += 1
                mod_effect.setdefault(w.type_id, w)
        # Order pilots so the most-flown hull leads, then alphabetically by ship and
        # character within each hull group (hull-less pilots sort last).
        plist.sort(
            key=lambda x: (-counts.get(x.ship_type_id or 0, 0), x.ship_name, x.character_name)
        )
        # Ships: most numerous first, ties broken alphabetically.
        ships = [
            CompositionShip(
                ship_type_id=sid,
                ship_name=ship_names.get(sid, "Unknown"),
                count=c,
                top_modules=[
                    mod_effect[wid]
                    for wid, _ in mod_counts.get(sid, Counter()).most_common(5)
                    if wid in mod_effect
                ],
            )
            for sid, c in sorted(
                counts.items(), key=lambda kv: (-kv[1], ship_names.get(kv[0], "Unknown"))
            )
        ]
        pilot_count = len({p.character_id for p in plist})
        sides.append(CompositionSide(side_kind=side_kind, pilot_count=pilot_count,
                                     ships=ships, pilots=plist))
    return CompositionResult(sides=sides)
