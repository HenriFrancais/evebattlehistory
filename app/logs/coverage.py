"""Per-user/character coverage: which fights are missing logs?

Public API
----------
    matrix = await br_coverage(session, settings, br_id)
    mine    = await my_coverage(session, settings, br_id, user_name)

E1 change
---------
Coverage now includes **log-only participants** (characters not on any killmail
but whose logs overlap a fight window and were associated).  Each CharacterCoverage
now carries ``on_killmail: bool`` and ``has_logs: bool``.

Semantics:
- A character is "in scope" for a roster user if they either participated in a
  killmail OR have LogEvents stamped with a fight in this BR.
- "covered" means has_logs=True (they uploaded and were associated) for ALL
  fights they are in scope for.
- A character with on_killmail=True but has_logs=False is still shown as missing.
- A character with on_killmail=False and has_logs=True is shown as covered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import BrFight, GamelogFile, LogEvent
from app.fights.participants import fight_participant_char_ids
from app.roster.snapshot import get_roster_store


@dataclass
class CharacterCoverage:
    character_id: int
    character_name: str
    participated_fights: list[int] = field(default_factory=list)
    fights_covered: list[int] = field(default_factory=list)
    fights_missing: list[int] = field(default_factory=list)
    covered: bool = False  # True iff all participated_fights are covered
    on_killmail: bool = False  # True iff character appears on ≥1 killmail in this BR (E1)
    has_logs: bool = False  # True iff character has ≥1 LogEvent stamped for this BR (E1)


@dataclass
class UserCoverage:
    user_name: str
    characters: list[CharacterCoverage] = field(default_factory=list)


def _coverage_to_dict(uc: UserCoverage) -> dict[str, Any]:
    return {
        "user_name": uc.user_name,
        "characters": [
            {
                "character_id": cc.character_id,
                "character_name": cc.character_name,
                "participated_fights": cc.participated_fights,
                "covered": cc.covered,
                "fights_covered": cc.fights_covered,
                "fights_missing": cc.fights_missing,
                "on_killmail": cc.on_killmail,
                "has_logs": cc.has_logs,
            }
            for cc in uc.characters
        ],
    }


async def br_coverage(
    session: AsyncSession,
    settings: Settings,
    br_id: str,
    user_name: str | None = None,
) -> list[UserCoverage]:
    """Return per-user/character coverage for every NV member with ≥1 participant character.

    A character is "in scope" if they participated in ≥1 killmail in the BR OR have
    ≥1 LogEvent stamped with a fight in the BR.  A character is "covered" for a fight
    if there is at least one parsed GamelogFile for that character with ≥1 LogEvent
    stamped with that fight_id.

    E1: each CharacterCoverage now includes on_killmail and has_logs flags.

    Parameters
    ----------
    user_name:
        When provided, limits the roster iteration to only the characters owned
        by that user.  This avoids scanning the full matrix when only a single
        user's coverage is needed (used by ``my_coverage``).  The returned list
        will have at most one entry.  When None (default), the full matrix is
        returned as before.
    """
    roster = await get_roster_store(settings).get()

    # 1. Collect all fight_ids in this BR
    fight_id_result = await session.execute(
        select(BrFight.fight_id).where(BrFight.br_id == br_id)
    )
    fight_ids = list(fight_id_result.scalars())

    if not fight_ids:
        return []

    # 2. For each fight, map character_id → set of fight_ids they participated in (killmail)
    km_char_to_fights: dict[int, set[int]] = {}
    for fight_id in fight_ids:
        participants = await fight_participant_char_ids(session, fight_id)
        for cid in participants:
            km_char_to_fights.setdefault(cid, set()).add(fight_id)

    # 3. Log-based participation: character_id → set of fight_ids with stamped events (E1)
    log_result = await session.execute(
        select(LogEvent.character_id, LogEvent.fight_id)
        .join(GamelogFile, GamelogFile.file_id == LogEvent.file_id)
        .where(
            LogEvent.fight_id.in_(fight_ids),
            LogEvent.character_id.is_not(None),
            GamelogFile.parse_status == "parsed",
        )
        .distinct()
    )
    log_char_to_fights: dict[int, set[int]] = {}
    for cid, fid in log_result.fetchall():
        if cid is not None and fid is not None:
            log_char_to_fights.setdefault(int(cid), set()).add(int(fid))

    # 4. Union of all char_ids that are "in scope"
    all_in_scope: dict[int, set[int]] = {}
    for cid, fights in km_char_to_fights.items():
        all_in_scope.setdefault(cid, set()).update(fights)
    for cid, fights in log_char_to_fights.items():
        all_in_scope.setdefault(cid, set()).update(fights)

    if not all_in_scope:
        return []

    # 5. Restrict to roster characters; optionally filter to one user.
    roster_char_ids = set(roster.char_to_user.keys())
    if user_name is not None:
        user_char_ids: set[int] = {
            cid for cid, uname in roster.char_to_user.items() if uname == user_name
        }
        roster_char_ids = roster_char_ids & user_char_ids
    relevant_chars = roster_char_ids & set(all_in_scope.keys())

    # 6. Build per-user coverage grouped by user
    char_name_lookup: dict[int, str] = {}
    for user in roster.users:
        for c in user.characters:
            char_name_lookup[c.character_id] = c.character_name

    user_map: dict[str, list[CharacterCoverage]] = {}

    for char_id in sorted(relevant_chars):
        uname = roster.char_to_user.get(char_id)
        if uname is None:
            continue

        all_fights_for_char = sorted(all_in_scope[char_id])
        # "covered" fights: those where log events exist (log_char_to_fights)
        log_fights = log_char_to_fights.get(char_id, set())
        fights_covered = sorted(fid for fid in all_fights_for_char if fid in log_fights)
        fights_missing = sorted(fid for fid in all_fights_for_char if fid not in log_fights)

        cc = CharacterCoverage(
            character_id=char_id,
            character_name=char_name_lookup.get(char_id, str(char_id)),
            participated_fights=all_fights_for_char,
            fights_covered=fights_covered,
            fights_missing=fights_missing,
            covered=len(fights_missing) == 0,
            on_killmail=bool(km_char_to_fights.get(char_id)),
            has_logs=bool(log_fights),
        )
        user_map.setdefault(uname, []).append(cc)

    return [UserCoverage(user_name=un, characters=chars) for un, chars in user_map.items()]


async def my_coverage(
    session: AsyncSession,
    settings: Settings,
    br_id: str,
    user_name: str,
) -> UserCoverage | None:
    """Return coverage for a single user, or None if they have no participating characters.

    Passes ``user_name`` through to ``br_coverage`` so only that user's characters
    are iterated; the full matrix is not scanned.
    """
    results = await br_coverage(session, settings, br_id, user_name=user_name)
    for uc in results:
        if uc.user_name == user_name:
            return uc
    return None
