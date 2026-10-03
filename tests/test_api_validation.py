"""Bad input is a 4xx, never a 500 or an unbounded query."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import func, select

from app.db.models import Aar, AarReaction, BattleReport
from tests.conftest import CREATOR_HEADERS, MEMBER_HEADERS


def _client(make_client, tmp_path):  # type: ignore[no-untyped-def]
    return make_client(DB_PATH=str(tmp_path / "t.db"))


@pytest.mark.parametrize("tree", [
    {"field": "isk_destroyed_total", "op": ">=", "value": {"x": 1}},
    {"field": "isk_destroyed_total", "op": ">=", "value": "lots"},
    {"field": "isk_destroyed_total", "op": ">=", "value": None},
    {"field": "capitals_involved", "op": "==", "value": "yes"},
    {"field": "started_at", "op": ">=", "value": "not a date"},
    {"field": "started_at", "op": "between", "value": ["2026-01-01", 5]},
])
def test_fight_filter_rejects_wrong_value_types(make_client, tmp_path, tree) -> None:  # type: ignore[no-untyped-def]
    client = _client(make_client, tmp_path)
    r = client.post("/api/fights/filter", json={"tree": tree}, headers=MEMBER_HEADERS)
    assert r.status_code == 400, r.text


@pytest.mark.parametrize("tree", [
    {"field": "fight_count", "op": ">=", "value": [1]},
    {"field": "result", "op": "==", "value": {"a": 1}},
    {"field": "result", "op": "in", "value": ["win", 3]},
    {"field": "battle_at", "op": "<=", "value": 12},
])
def test_br_filter_rejects_wrong_value_types(make_client, tmp_path, tree) -> None:  # type: ignore[no-untyped-def]
    client = _client(make_client, tmp_path)
    r = client.post("/api/brs/filter", json={"tree": tree}, headers=MEMBER_HEADERS)
    assert r.status_code == 400, r.text


def test_valid_filters_still_work(make_client, tmp_path) -> None:  # type: ignore[no-untyped-def]
    client = _client(make_client, tmp_path)
    ok = [
        ("/api/fights/filter", {"field": "isk_destroyed_total", "op": ">=", "value": 1.5}),
        ("/api/fights/filter", {"field": "started_at", "op": ">=", "value": "2026-01-01T00:00:00Z"}),
        ("/api/fights/filter", {"field": "capitals_involved", "op": "==", "value": True}),
        ("/api/brs/filter", {"field": "result", "op": "in", "value": ["win", "tie"]}),
        ("/api/brs/filter", {"field": "battle_at", "op": "between",
                             "value": ["2026-01-01", "2026-12-31T23:59:59"]}),
    ]
    for url, tree in ok:
        r = client.post(url, json={"tree": tree}, headers=MEMBER_HEADERS)
        assert r.status_code == 200, (tree, r.text)


def test_filter_tree_depth_and_width_are_bounded(make_client, tmp_path) -> None:  # type: ignore[no-untyped-def]
    client = _client(make_client, tmp_path)
    leaf = {"field": "fight_count", "op": ">=", "value": 1}
    deep: dict = leaf  # type: ignore[type-arg]
    for _ in range(12):
        deep = {"op": "and", "clauses": [deep]}
    assert client.post("/api/brs/filter", json={"tree": deep},
                       headers=MEMBER_HEADERS).status_code == 400
    wide = {"op": "or", "clauses": [leaf] * 200}
    assert client.post("/api/brs/filter", json={"tree": wide},
                       headers=MEMBER_HEADERS).status_code == 400


def test_fight_filter_results_are_capped() -> None:
    from app.analytics.filters import MAX_FILTER_RESULTS, compile_fight_filter

    stmt = compile_fight_filter({"field": "isk_destroyed_total", "op": ">=", "value": 0})
    assert stmt._limit == MAX_FILTER_RESULTS


def _seed_br(db) -> None:  # type: ignore[no-untyped-def]
    import sqlite3

    with sqlite3.connect(db) as c:
        c.execute(
            "insert into battle_report (br_id, source, source_url, source_ref, created_by_user,"
            " status, progress_pct, created_at, km_count, km_expected, our_isk_destroyed,"
            " our_isk_lost, fight_count) values ('b1','zkb','x','r','t','ready',100,"
            " '2026-01-01 00:00:00', 0, 0, 0, 0, 0)"
        )


def test_aar_and_comment_bodies_are_length_capped(make_client, tmp_path) -> None:  # type: ignore[no-untyped-def]
    client = _client(make_client, tmp_path)
    _seed_br(tmp_path / "t.db")
    too_long = client.put("/api/brs/b1/aar", json={"body": "x" * 60_001}, headers=CREATOR_HEADERS)
    assert too_long.status_code == 422
    assert client.put("/api/brs/b1/aar", json={"body": "fine"},
                      headers=CREATOR_HEADERS).status_code == 200
    long_comment = client.post("/api/brs/b1/aar/comments", json={"body": "y" * 5_001},
                               headers=MEMBER_HEADERS)
    assert long_comment.status_code == 422
    assert client.post("/api/brs/b1/aar/comments", json={"body": "gg"},
                       headers=MEMBER_HEADERS).status_code == 200


async def test_adding_the_same_reaction_twice_is_not_an_error(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.api.aar import _add_reaction

    now = dt.datetime.now(dt.UTC)
    async with db_session_maker() as session:
        session.add(BattleReport(br_id="b1", source="x", source_url="x", source_ref="r",
                                 created_by_user="t", status="ready", created_at=now))
        aar = Aar(br_id="b1", body="b", created_by_user="t", updated_by_user="t",
                  created_at=now, updated_at=now)
        session.add(aar)
        await session.flush()
        # Two concurrent toggles both saw "absent" and both insert.
        await _add_reaction(session, aar.aar_id, "aar", aar.aar_id, "LineMember", "👍")
        await _add_reaction(session, aar.aar_id, "aar", aar.aar_id, "LineMember", "👍")
        await session.commit()
        n = (await session.execute(select(func.count()).select_from(AarReaction))).scalar_one()
    assert n == 1
