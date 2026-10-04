"""Per-pilot bucket timeline: shape, x axis shared with the fleet timeline, tackle
counted from a pilot's own log lines, and FC/HC-only visibility of other pilots."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select, update

from app.config import get_settings
from tests.conftest import CREATOR_HEADERS, MEMBER_HEADERS
from tests.test_association import _insert_gamelog_file, _insert_log_events
from tests.test_privacy_gating import (  # noqa: F401  (privacy_br is a fixture)
    FIGHT_END,
    FIGHT_START,
    MEMBER_CHAR,
    RAZOK_CHAR,
    privacy_br,
)

T30 = int((FIGHT_START + dt.timedelta(seconds=30)).replace(tzinfo=dt.UTC).timestamp())


@pytest.fixture
async def bucketed_br(privacy_br):  # type: ignore[no-untyped-def]  # noqa: F811
    """privacy_br (both roster characters logged 500 dmg / 300 rep / 40 neut, out) plus
    extra lines for RAZOK_CHAR: a second smaller hit, a miss, a tackle he applied and a
    tackle he merely observed. Buckets are built as association would."""
    from app.db.engine import get_sessionmaker
    from app.db.models import LogEvent
    from app.logs.associate import _rebuild_buckets_for_pairs

    client, br_id, fight_id = privacy_br
    ts = [FIGHT_START + dt.timedelta(seconds=31)]
    async with get_sessionmaker(get_settings())() as session:
        file_id = await _insert_gamelog_file(
            session, RAZOK_CHAR, log_start=FIGHT_START, log_end=FIGHT_END
        )
        await _insert_log_events(session, file_id, RAZOK_CHAR, ts, "damage", "out", 120.0)
        await _insert_log_events(session, file_id, RAZOK_CHAR, ts, "miss", "out", 0.0)
        own = await _insert_log_events(session, file_id, RAZOK_CHAR, ts, "scram", "out", 0.0)
        await _insert_log_events(session, file_id, RAZOK_CHAR, ts, "scram", "in", 0.0)  # observed
        await session.execute(
            update(LogEvent).where(LogEvent.event_id.in_(own)).values(authoritative=True)
        )
        await session.execute(
            update(LogEvent).where(LogEvent.file_id == file_id).values(fight_id=fight_id)
        )
        await _rebuild_buckets_for_pairs(
            session, {(fight_id, RAZOK_CHAR), (fight_id, MEMBER_CHAR)}
        )
        await session.commit()
        assert (await session.execute(select(LogEvent.event_id).limit(1))).first()
    return client, br_id


def _series(pilot: dict, effect: str, direction: str = "out") -> dict:  # type: ignore[type-arg]
    return next(
        s for s in pilot["series"] if s["effect_type"] == effect and s["direction"] == direction
    )


def _pilot(body: dict, cid: int) -> dict:  # type: ignore[type-arg]
    return next(p for p in body["pilots"] if p["character_id"] == cid)


async def test_fc_gets_every_pilot_with_hit_statistics(bucketed_br) -> None:  # type: ignore[no-untyped-def]
    client, br_id = bucketed_br
    body = client.get(f"/api/brs/{br_id}/pilot-timeline", headers=CREATOR_HEADERS).json()

    assert body["scope"] == "all"
    assert body["bucket_seconds"] == 5
    assert {p["character_id"] for p in body["pilots"]} == {RAZOK_CHAR, MEMBER_CHAR}
    razok = _pilot(body, RAZOK_CHAR)
    assert razok["is_self"] is True and _pilot(body, MEMBER_CHAR)["is_self"] is False

    dmg = _series(razok, "damage")
    assert [body["x"][i] for i in dmg["idx"]] == [T30]
    assert (dmg["sum"], dmg["count"], dmg["min"], dmg["max"]) == ([620.0], [2], [120.0], [500.0])
    miss = _series(razok, "miss")
    assert (miss["sum"], miss["count"], miss["min"], miss["max"]) == ([1.0], [1], [None], [None])


async def test_x_axis_matches_the_fleet_timeline(bucketed_br) -> None:  # type: ignore[no-untyped-def]
    client, br_id = bucketed_br
    pilots = client.get(f"/api/brs/{br_id}/pilot-timeline", headers=CREATOR_HEADERS).json()
    fleet = client.get(f"/api/brs/{br_id}/fleet-timeline", headers=CREATOR_HEADERS).json()
    assert pilots["x"] == fleet["x"]


async def test_tackle_counts_only_the_pilots_own_lines(bucketed_br) -> None:  # type: ignore[no-untyped-def]
    client, br_id = bucketed_br
    body = client.get(f"/api/brs/{br_id}/pilot-timeline", headers=CREATOR_HEADERS).json()
    razok = _pilot(body, RAZOK_CHAR)
    assert _series(razok, "scram", "out")["count"] == [1]
    # The tackle he only observed on someone else is not his.
    assert not [s for s in razok["series"] if s["effect_type"] == "scram" and s["direction"] == "in"]


async def test_member_gets_every_pilots_summary_series(bucketed_br) -> None:  # type: ignore[no-untyped-def]
    """The stat summaries are for everyone: a member receives the same per-pilot
    series as an FC. `scope` says whose per-target BREAKDOWN they may open."""
    client, br_id = bucketed_br
    body = client.get(f"/api/brs/{br_id}/pilot-timeline", headers=MEMBER_HEADERS).json()
    fc = client.get(f"/api/brs/{br_id}/pilot-timeline", headers=CREATOR_HEADERS).json()

    assert body["scope"] == "own" and fc["scope"] == "all"
    assert {p["character_id"] for p in body["pilots"]} == {RAZOK_CHAR, MEMBER_CHAR}
    assert _pilot(body, MEMBER_CHAR)["is_self"] is True
    assert _pilot(body, RAZOK_CHAR)["is_self"] is False
    assert _pilot(body, RAZOK_CHAR)["series"] == _pilot(fc, RAZOK_CHAR)["series"]


async def test_member_cannot_open_another_pilots_breakdown(bucketed_br) -> None:  # type: ignore[no-untyped-def]
    """Who a pilot shot and with what stays FC/HC-only, except for your own characters."""
    client, br_id = bucketed_br
    lo, hi = T30 - 60, T30 + 60
    url = f"/api/brs/{br_id}/characters/{{}}/snapshot?from_ts={lo}&to_ts={hi}"
    assert client.get(url.format(RAZOK_CHAR), headers=MEMBER_HEADERS).status_code == 403
    assert client.get(url.format(MEMBER_CHAR), headers=MEMBER_HEADERS).status_code == 200
    assert client.get(url.format(RAZOK_CHAR), headers=CREATOR_HEADERS).status_code == 200


async def test_ship_falls_back_to_the_fc_assigned_hull(bucketed_br) -> None:  # type: ignore[no-untyped-def]
    """RAZOK_CHAR is on the killmail only as an attacker with no hull recorded; the
    hull an FC assigned on the Involved tab must show in the stats table too."""
    from app.api.derived_cache import bump_derived
    from app.db.engine import get_sessionmaker
    from app.db.models import BrCharShip, InventoryType

    client, br_id = bucketed_br
    before = client.get(f"/api/brs/{br_id}/pilot-timeline", headers=CREATOR_HEADERS).json()
    assert _pilot(before, RAZOK_CHAR)["ship_name"] is None
    async with get_sessionmaker(get_settings())() as session:
        session.add(InventoryType(type_id=11987, name="Guardian", group_name="Logistics", category_id=6))
        session.add(BrCharShip(
            br_id=br_id, character_id=RAZOK_CHAR, ship_type_id=11987,
            set_by_user="Ra'zok", set_at=dt.datetime.now(dt.UTC),
        ))
        await session.commit()
    bump_derived()

    after = client.get(f"/api/brs/{br_id}/pilot-timeline", headers=CREATOR_HEADERS).json()
    razok = _pilot(after, RAZOK_CHAR)
    assert (razok["ship_type_id"], razok["ship_name"]) == (11987, "Guardian")


async def test_unknown_br_is_404(bucketed_br) -> None:  # type: ignore[no-untyped-def]
    client, _ = bucketed_br
    assert client.get("/api/brs/nope/pilot-timeline", headers=CREATOR_HEADERS).status_code == 404
