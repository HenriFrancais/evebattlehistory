"""Characterisation of the affiliation lookups in persist_killmails and the
known-name check in resolve_log_characters, pinned before they were made
linear-time."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from app.db.models import Character, Corporation


def _km(km_id: int, victim: tuple[int, int, int | None], attackers: list[tuple[int, int, int | None]]):  # type: ignore[no-untyped-def]
    v_char, v_corp, v_alli = victim
    return {
        "killmail_id": km_id, "killmail_time": "2026-06-01T00:00:00Z", "solar_system_id": 1,
        "victim": {"character_id": v_char, "corporation_id": v_corp, "alliance_id": v_alli,
                   "ship_type_id": 40, "damage_taken": 1, "items": []},
        "attackers": [
            {"character_id": c, "corporation_id": co, **({"alliance_id": a} if a else {}),
             "damage_done": 1, "final_blow": False}
            for c, co, a in attackers
        ],
    }


async def test_first_seen_affiliation_wins_across_victims_and_attackers(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.ingest.persist import persist_killmails

    kms = [
        # char 10 first appears as an ATTACKER in corp 21 / alliance 31 …
        _km(1, (99, 29, None), [(10, 21, 31)]),
        # … and later as a victim in corp 22 (no alliance): the first sighting wins.
        _km(2, (10, 22, None), [(11, 23, None)]),
        # corp 23 has no alliance on km 2 but one on km 3 → the first NON-NULL wins.
        _km(3, (12, 23, 33), []),
    ]
    async with db_session_maker() as session:
        await persist_killmails(session, kms, {})
        await session.commit()
        chars = {c.character_id: (c.corporation_id, c.alliance_id)
                 for c in (await session.execute(select(Character))).scalars()}
        corps = {c.corporation_id: c.alliance_id
                 for c in (await session.execute(select(Corporation))).scalars()}
    assert chars[10] == (21, 31)
    assert chars[11] == (23, None)
    assert corps[23] == 33
    assert corps[21] == 31
    assert corps[22] is None


async def test_known_names_are_not_re_resolved_regardless_of_case(db_session_maker, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.config import get_settings
    from app.fights.offbr_resolve import resolve_log_characters

    asked: list[list[str]] = []

    class _Esi:
        async def resolve_ids(self, names: list[str]) -> dict[str, int]:
            asked.append(sorted(names))
            return {}

    async with db_session_maker() as session:
        session.add(Character(character_id=1, name="Known Pilot",
                              last_seen_at=dt.datetime.now(dt.UTC)))
        await session.flush()
        n = await resolve_log_characters(
            session, get_settings(), {"known pilot", "KNOWN PILOT", "New Pilot"}, esi=_Esi()
        )
    assert n == 0
    assert asked == [["New Pilot"]]


def test_resolve_does_not_load_the_whole_character_table() -> None:
    import inspect

    import app.fights.offbr_resolve as mod

    src = inspect.getsource(mod.resolve_log_characters)
    assert "Character.name.is_not(None)" not in src
