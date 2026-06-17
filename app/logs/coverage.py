"""Per-user/character coverage: which fights are missing logs?

Public API
----------
    matrix = await br_coverage(session, settings, br_id)
    mine    = await my_coverage(session, settings, br_id, user_name)
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

    A character is "covered" for a fight if there is at least one parsed
    GamelogFile for that character with ≥1 LogEvent stamped with that fight_id.

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

    # 2. For each fight, map character_id → set of fight_ids they participated in
    char_fought_in: dict[int, set[int]] = {}
    for fight_id in fight_ids:
        participants = await fight_participant_char_ids(session, fight_id)
        for cid in participants:
            char_fought_in.setdefault(cid, set()).add(fight_id)

    if not char_fought_in:
        return []

    # 3. For each (character_id, fight_id) pair, check whether a log exists
    #    (parse_status="parsed" AND ≥1 LogEvent with fight_id stamped)
    # Collect distinct character_ids that are in the roster AND participated.
    # When user_name is set, only consider that user's characters (optimisation
    # for my_coverage — avoids a full-matrix scan).
    roster_char_ids = set(roster.char_to_user.keys())
    if user_name is not None:
        # Find which char_ids belong to this user in the roster
        user_char_ids: set[int] = {
            cid for cid, uname in roster.char_to_user.items() if uname == user_name
        }
        roster_char_ids = roster_char_ids & user_char_ids
    relevant_chars = roster_char_ids & set(char_fought_in.keys())

    # Load all (character_id, fight_id) pairs where log coverage exists
    covered_pairs: set[tuple[int, int]] = set()
    if relevant_chars:
        events_result = await session.execute(
            select(LogEvent.character_id, LogEvent.fight_id)
            .join(GamelogFile, GamelogFile.file_id == LogEvent.file_id)
            .where(
                LogEvent.character_id.in_(list(relevant_chars)),
                LogEvent.fight_id.in_(fight_ids),
                GamelogFile.parse_status == "parsed",
            )
            .distinct()
        )
        for cid, fid in events_result.fetchall():
            if cid is not None and fid is not None:
                covered_pairs.add((int(cid), int(fid)))

    # 4. Build per-user coverage grouped by user
    # user_name → list of CharacterCoverage
    user_map: dict[str, list[CharacterCoverage]] = {}

    # Build name lookup: character_id → character_name from roster
    char_name_lookup: dict[int, str] = {}
    for user in roster.users:
        for c in user.characters:
            char_name_lookup[c.character_id] = c.character_name

    for char_id in sorted(relevant_chars):
        uname = roster.char_to_user.get(char_id)
        if uname is None:
            continue
        fights_for_char = sorted(char_fought_in[char_id])
        fights_covered = sorted(
            fid for fid in fights_for_char if (char_id, fid) in covered_pairs
        )
        fights_missing = sorted(
            fid for fid in fights_for_char if (char_id, fid) not in covered_pairs
        )

        cc = CharacterCoverage(
            character_id=char_id,
            character_name=char_name_lookup.get(char_id, str(char_id)),
            participated_fights=fights_for_char,
            fights_covered=fights_covered,
            fights_missing=fights_missing,
            covered=len(fights_missing) == 0,
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
