"""Read-time identification of off-BR participants from logs.

A participant is "off-BR" if they never appear on a killmail in the BR but are
identifiable from logs stamped to the BR's fights — either because they uploaded
logs (log-owner) or because they appear as a counterparty (other/source/target)
in someone else's logs and resolve to a known ``Character``.

Pure read: uses only persisted ``Character``/``LogEvent``/``InventoryType`` data
(ESI resolution of counterparty names happens at upload time, see
``offbr_resolve``). No ESI, no commit.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import BrFight, Character, InventoryType, LogEvent
from app.fights.participants import br_logged_char_ids, fight_participant_char_ids
from app.observability.logging import log
from app.roster.snapshot import get_roster_store


@dataclass
class OffBrChar:
    character_id: int
    character_name: str | None
    alliance_id: int | None
    corporation_id: int | None
    detected_ship_type_id: int | None
    user_name: str | None
    source: str  # "log_owner" | "counterparty"


async def offbr_log_characters(
    session: AsyncSession, settings: Settings, br_id: str
) -> list[OffBrChar]:
    fight_ids = list(
        (await session.execute(select(BrFight.fight_id).where(BrFight.br_id == br_id))).scalars()
    )
    if not fight_ids:
        return []

    # On-BR (killmail) characters — the exclusion set.
    on_br: set[int] = set()
    for fid in fight_ids:
        on_br |= await fight_participant_char_ids(session, fid)

    # Source A: log-owners stamped to the BR's fights.
    log_owners = await br_logged_char_ids(session, br_id)

    # Source B: counterparty names in the BR's fight logs, exact-matched to a Character.
    name_rows = (
        await session.execute(
            select(LogEvent.other_name, LogEvent.source_name, LogEvent.target_name).where(
                LogEvent.fight_id.in_(fight_ids)
            )
        )
    ).all()
    cp_names = {v for row in name_rows for v in row if v}
    # Drop counterparty tokens that are actually SDE inventory types (ships, drones,
    # missiles, charges). EVE players sometimes share a name with an item, so a log
    # token like "Hobgoblin II" or "Caldari Navy Warden" can resolve to a
    # coincidentally named character — but in a combat log it is overwhelmingly the
    # item, not a participant. Only real characters may appear in the fleets
    # "By character" / "By user" lists. (Log-owners are unaffected: a real pilot who
    # uploaded a gamelog is still identified via br_logged_char_ids above.)
    if cp_names:
        inv_lower = {
            (nm or "").lower()
            for (nm,) in (
                await session.execute(
                    select(InventoryType.name).where(InventoryType.name.in_(cp_names))
                )
            ).all()
        }
        cp_names = {n for n in cp_names if n.lower() not in inv_lower}
    name_to_cid: dict[str, int] = {}
    if cp_names:
        lowered = {n.lower() for n in cp_names}
        for cid, nm in (
            await session.execute(
                select(Character.character_id, Character.name).where(Character.name.is_not(None))
            )
        ).all():
            if nm and nm.lower() in lowered:
                name_to_cid[nm.lower()] = cid
    counterparty_cids = {name_to_cid[n.lower()] for n in cp_names if n.lower() in name_to_cid}

    candidates = (log_owners | counterparty_cids) - on_br
    if not candidates:
        return []

    # Character rows (name + affiliation).
    chars = {
        c.character_id: c
        for c in (
            await session.execute(
                select(Character).where(Character.character_id.in_(candidates))
            )
        ).scalars()
    }

    # Detected ship: most common other_ship_name where the candidate is the counterparty.
    cid_by_lower_name = {(c.name or "").lower(): cid for cid, c in chars.items() if c.name}
    ship_counts: dict[int, Counter[str]] = {}
    for other_name, other_ship in (
        await session.execute(
            select(LogEvent.other_name, LogEvent.other_ship_name).where(
                LogEvent.fight_id.in_(fight_ids),
                LogEvent.other_name.is_not(None),
                LogEvent.other_ship_name.is_not(None),
            )
        )
    ).all():
        cid = cid_by_lower_name.get((other_name or "").lower())
        if cid is not None:
            ship_counts.setdefault(cid, Counter())[other_ship] += 1
    # Resolve detected ship names → type_ids.
    wanted_ship_names = {c.most_common(1)[0][0] for c in ship_counts.values() if c}
    ship_name_to_id: dict[str, int] = {}
    if wanted_ship_names:
        for inv in (
            await session.execute(
                select(InventoryType).where(InventoryType.name.in_(wanted_ship_names))
            )
        ).scalars():
            ship_name_to_id[inv.name] = inv.type_id

    # Roster user names (best-effort).
    char_to_user: dict[int, str] = {}
    try:
        roster = await get_roster_store(settings).get()
        char_to_user = dict(roster.char_to_user)
    except Exception as exc:  # roster unavailable
        log.warning("offbr.roster_unavailable", error=str(exc))

    out: list[OffBrChar] = []
    for cid in sorted(candidates):
        c = chars.get(cid)
        detected = None
        if ship_counts.get(cid):
            detected = ship_name_to_id.get(ship_counts[cid].most_common(1)[0][0])
        out.append(
            OffBrChar(
                character_id=cid,
                character_name=(c.name if c else None),
                alliance_id=(c.alliance_id if c else None),
                corporation_id=(c.corporation_id if c else None),
                detected_ship_type_id=detected,
                user_name=char_to_user.get(cid),
                source=("log_owner" if cid in log_owners else "counterparty"),
            )
        )
    return out
