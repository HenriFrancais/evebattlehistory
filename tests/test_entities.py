"""Entity directory, snapshot hit statistics and composition affiliations."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import update

from app.config import get_settings
from tests.conftest import CREATOR_HEADERS, MEMBER_HEADERS
from tests.test_association import _insert_gamelog_file, _insert_log_events
from tests.test_privacy_gating import (  # noqa: F401  (privacy_br is a fixture)
    FIGHT_END,
    FIGHT_START,
    MEMBER_CHAR,
    RAZOK_CHAR,
    T0,
    T1,
    privacy_br,
)

CORP, ALLI = 98000001, 99000001


async def _affiliate_and_log(fight_id: int) -> None:
    """Put MEMBER_CHAR (the victim) in a corp+alliance with tickers, and give RAZOK_CHAR
    extra lines: hits of 120 and 80 on 'Bad Guy', two misses, and names with tickers."""
    from app.db.engine import get_sessionmaker
    from app.db.models import Alliance, Corporation, Killmail, LogEvent

    now = dt.datetime(2026, 1, 1)
    ts = [FIGHT_START + dt.timedelta(seconds=40)]
    async with get_sessionmaker(get_settings())() as session:
        session.add(Alliance(alliance_id=ALLI, name="Test Alliance", ticker="TALLY", last_seen_at=now))
        await session.flush()
        session.add(Corporation(
            corporation_id=CORP, name="Test Corp", ticker="TCORP", alliance_id=ALLI, last_seen_at=now,
        ))
        await session.flush()
        await session.execute(update(Killmail).values(
            victim_corporation_id=CORP, victim_alliance_id=ALLI, total_value=1_500_000.0,
        ))
        file_id = await _insert_gamelog_file(
            session, RAZOK_CHAR, log_start=FIGHT_START, log_end=FIGHT_END
        )
        ids = await _insert_log_events(session, file_id, RAZOK_CHAR, ts, "damage", "out", 120.0)
        ids += await _insert_log_events(session, file_id, RAZOK_CHAR, ts, "damage", "out", 80.0)
        ids += await _insert_log_events(session, file_id, RAZOK_CHAR, ts * 2, "miss", "out", 0.0)
        await session.execute(update(LogEvent).where(LogEvent.event_id.in_(ids)).values(
            fight_id=fight_id, other_name="Bad Guy", other_corp_ticker="EVIL",
            other_alliance_ticker="BAD", quality="Hits",
        ))
        odd = await _insert_log_events(session, file_id, RAZOK_CHAR, ts, "damage", "out", 1.0)
        await session.execute(update(LogEvent).where(LogEvent.event_id.in_(odd)).values(
            fight_id=fight_id, other_name="Bad Guy", other_corp_ticker="WRONG", quality="Hits",
        ))
        await session.commit()


async def test_directory_lists_characters_with_tickers(privacy_br) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    client, br_id, fight_id = privacy_br
    await _affiliate_and_log(fight_id)
    body = client.get(f"/api/brs/{br_id}/entities", headers=CREATOR_HEADERS).json()

    member = next(c for c in body["characters"] if c["character_id"] == MEMBER_CHAR)
    assert (member["corporation_id"], member["alliance_id"]) == (CORP, ALLI)
    assert body["corporations"] == [
        {"corporation_id": CORP, "name": "Test Corp", "ticker": "TCORP", "alliance_id": ALLI}
    ]
    assert body["alliances"] == [{"alliance_id": ALLI, "name": "Test Alliance", "ticker": "TALLY"}]


async def test_name_without_a_character_falls_back_to_the_most_seen_log_tickers(privacy_br) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    client, br_id, fight_id = privacy_br
    await _affiliate_and_log(fight_id)
    body = client.get(f"/api/brs/{br_id}/entities", headers=CREATOR_HEADERS).json()
    assert {"name": "Bad Guy", "corp_ticker": "EVIL", "alliance_ticker": "BAD"} in body["by_name"]
    assert sum(1 for n in body["by_name"] if n["name"] == "Bad Guy") == 1


async def test_directory_is_readable_by_members(privacy_br) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    client, br_id, fight_id = privacy_br
    await _affiliate_and_log(fight_id)
    creator = client.get(f"/api/brs/{br_id}/entities", headers=CREATOR_HEADERS).json()
    member = client.get(f"/api/brs/{br_id}/entities", headers=MEMBER_HEADERS)
    assert member.status_code == 200 and member.json() == creator


async def test_snapshot_rows_carry_hit_statistics_and_misses(privacy_br) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    client, br_id, fight_id = privacy_br
    await _affiliate_and_log(fight_id)
    body = client.get(
        f"/api/brs/{br_id}/characters/{RAZOK_CHAR}/snapshot?from_ts={T0}&to_ts={T1}",
        headers=CREATOR_HEADERS,
    ).json()
    row = next(
        r for r in body["rows"] if r["effect_type"] == "damage" and r["target_name"] == "Bad Guy"
    )
    assert (row["value"], row["hits"], row["min_hit"], row["max_hit"]) == (201.0, 3, 1.0, 120.0)
    assert row["quality_counts"] == {"Hits": 3, "Misses": 2}
    assert row["quality"] == "Hits"
    assert not [r for r in body["rows"] if r["effect_type"] == "miss"]
    rep = next(r for r in body["rows"] if r["effect_type"] == "rep_armor")
    assert (rep["hits"], rep["min_hit"], rep["max_hit"]) == (1, 300.0, 300.0)


async def test_composition_carries_affiliation_and_side_losses(privacy_br) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    client, br_id, fight_id = privacy_br
    await _affiliate_and_log(fight_id)
    body = client.get(f"/api/brs/{br_id}/composition", headers=CREATOR_HEADERS).json()
    pilots = {p["character_id"]: (s, p) for s in body["sides"] for p in s["pilots"]}
    side, member = pilots[MEMBER_CHAR]
    assert (member["corporation_id"], member["alliance_id"]) == (CORP, ALLI)
    assert (side["losses"], side["isk_lost"]) == (1, 1_500_000.0)
    other_side, _ = pilots[RAZOK_CHAR]
    if other_side is not side:
        assert (other_side["losses"], other_side["isk_lost"]) == (0, 0.0)
