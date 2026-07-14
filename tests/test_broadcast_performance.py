"""Performance analytics assembly + access-gating tests."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert

from app.analytics.broadcasts import (
    BroadcastMetrics,
    BroadcastSummary,
    DeathBroadcastRow,
    PilotSwitchRow,
    QualityReport,
    RepRequestMetrics,
    RepRequestRow,
    TargetCallMetrics,
)
from app.analytics.composition import CompositionPilot
from app.analytics.performance import assemble_performance
from app.db.models import (
    BattleReport,
    Broadcast,
    BroadcastFile,
    BrFight,
    Character,
    Fight,
    GamelogFile,
    LogEvent,
    SolarSystem,
)
from tests.conftest import CREATOR_HEADERS, MEMBER_HEADERS, TEST_TOKEN

# The member fixture's own character id (from MEMBER_HEADERS main_character_id).
MEMBER_CHAR = 95000001
OTHER_CHAR = 424242


def _pilot(cid: int, name: str, dmg: int = 0, reps: float = 0.0, kills: int = 0) -> CompositionPilot:
    return CompositionPilot(
        character_id=cid, character_name=name, ship_type_id=None, ship_name="",
        lost=False, reship=False, user_name=None, killmail_id=None, weapons=[],
        damage_done=dmg, kill_count=kills, reps_out=reps, has_logs=True,
    )


def _metrics() -> BroadcastMetrics:
    return BroadcastMetrics(
        has_broadcasts=True,
        summary=BroadcastSummary(n_targets=1, n_reps=1),
        targets=TargetCallMetrics(
            rows=[],
            per_pilot=[
                PilotSwitchRow(character_id=MEMBER_CHAR, character_name="Me", calls_fired=2,
                               median_switch_s=5.0, compliance_rate=1.0),
                PilotSwitchRow(character_id=OTHER_CHAR, character_name="Other", calls_fired=2,
                               median_switch_s=9.0, compliance_rate=0.5),
            ],
        ),
        reps=RepRequestMetrics(rows=[
            # Repper is "Me" (MEMBER_CHAR) applying the rep → logi metric attributed to them.
            RepRequestRow(broadcast_id=1, ts=dt.datetime(2026, 7, 11, 23, 0), subject_name="Other",
                          subject_character_id=OTHER_CHAR, resource="shield", fight_id=1,
                          logi_response_s=4.0, repper_name="Me", justified=True, reaction_s=3.0,
                          damage_lead_s=6.0, broadcast_late=False, has_log=True),
        ]),
        quality=QualityReport(),
        deaths=[
            DeathBroadcastRow(character_id=OTHER_CHAR, character_name="Other", ship="Loki",
                              killmail_id=1, ts=dt.datetime(2026, 7, 11, 23, 1), fight_id=1,
                              classification="late_broadcast", last_broadcast_delta_s=2.0),
        ],
    )


def test_assemble_filters_to_self_for_non_elevated():
    pilots = [_pilot(MEMBER_CHAR, "Me", dmg=1000, kills=2), _pilot(OTHER_CHAR, "Other", dmg=5000)]
    perf = assemble_performance(pilots, _metrics(), {MEMBER_CHAR}, elevated=False)
    # Non-elevated: only own character rows.
    assert [r.character_id for r in perf.characters] == [MEMBER_CHAR]
    me = perf.characters[0]
    assert me.is_self is True
    assert me.target_median_switch_s == 5.0
    assert me.logi_response_median_s == 4.0
    # Distributions are computed from ALL friendly pilots (anonymous) even when filtered.
    assert sorted(perf.distributions.target_switch_s) == [5.0, 9.0]


def test_assemble_shows_all_for_elevated():
    pilots = [_pilot(MEMBER_CHAR, "Me", dmg=1000), _pilot(OTHER_CHAR, "Other", dmg=5000)]
    perf = assemble_performance(pilots, _metrics(), {MEMBER_CHAR}, elevated=True)
    ids = {r.character_id for r in perf.characters}
    assert ids == {MEMBER_CHAR, OTHER_CHAR}
    other = next(r for r in perf.characters if r.character_id == OTHER_CHAR)
    assert other.deaths_flagged == 1
    # Sorted by damage desc → Other (5000) first.
    assert perf.characters[0].character_id == OTHER_CHAR


# ---------------------------------------------------------------------------
# API access gating
# ---------------------------------------------------------------------------

BR_ID = "perf-api"


async def _seed(session) -> None:  # type: ignore[no-untyped-def]
    session.add(SolarSystem(system_id=1, name="J1"))
    session.add(BattleReport(br_id=BR_ID, source="demo", source_url="x", source_ref="r",
                             created_by_user="t", status="ready", progress_pct=100,
                             created_at=dt.datetime(2026, 7, 11, 23, 30),
                             battle_at=dt.datetime(2026, 7, 11, 23, 0)))
    session.add(Fight(fight_id=1, system_id=1,
                      started_at=dt.datetime(2026, 7, 11, 22, 55),
                      ended_at=dt.datetime(2026, 7, 11, 23, 30)))
    session.add(BrFight(br_id=BR_ID, fight_id=1, seq=0))
    session.add(Character(character_id=MEMBER_CHAR, name="Line Member", last_seen_at=dt.datetime(2026, 7, 11, 23, 0)))
    session.add(GamelogFile(file_id=1, uploaded_by_user="t", resolved_via="x", stored_path="/g",
                            sha256="g1", mime="text/plain", size=1, parse_status="parsed",
                            event_count=0, uploaded_at=dt.datetime(2026, 7, 11, 23, 0)))
    bf = BroadcastFile(broadcast_file_id=1, br_id=BR_ID, uploaded_by_user="t", stored_path="/x",
                       sha256="s1", mime="text/plain", size=1, parse_status="parsed",
                       broadcast_count=1, superseded=False, uploaded_at=dt.datetime(2026, 7, 11, 23, 0))
    session.add(bf)
    await session.flush()
    t0 = dt.datetime(2026, 7, 11, 23, 0, 0)
    await session.execute(insert(Broadcast), [dict(
        file_id=1, br_id=BR_ID, fight_id=1, ts=t0, kind="target", subject_name="Foe",
        subject_ship="Rattlesnake", subject_character_id=None, seq=0, raw_line="r")])
    # A fleet member fires on the called target 6s later → per_pilot has this member.
    await session.execute(insert(LogEvent), [dict(
        file_id=1, character_id=MEMBER_CHAR, ts=t0 + dt.timedelta(seconds=6), direction="out",
        effect_type="damage", other_name="Foe", fight_id=1, dedupe_suppressed=False,
        authoritative=False)])


async def _make_app(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.main import create_app

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    get_settings.cache_clear(); get_app_config.cache_clear(); reset_engine_for_tests()
    settings = get_settings()
    await init_models(settings)
    sm = get_sessionmaker(settings)
    async with sm() as session:
        await _seed(session)
        await session.commit()
    return create_app()


@pytest.mark.asyncio
async def test_metrics_per_character_gated_to_elevated(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    app = await _make_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        member = client.get(f"/api/brs/{BR_ID}/broadcasts/metrics", headers=MEMBER_HEADERS).json()
        fc = client.get(f"/api/brs/{BR_ID}/broadcasts/metrics", headers=CREATOR_HEADERS).json()
        # Aggregate summary is visible to both.
        assert member["summary"]["n_targets"] == fc["summary"]["n_targets"]
        # Per-character discipline gated: empty for the member, populated for FC/HC.
        assert member["targets"]["per_pilot"] == []
        assert len(fc["targets"]["per_pilot"]) >= 1


@pytest.mark.asyncio
async def test_performance_endpoint_elevated_flag(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    app = await _make_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        member = client.get(f"/api/brs/{BR_ID}/performance", headers=MEMBER_HEADERS).json()
        fc = client.get(f"/api/brs/{BR_ID}/performance", headers=CREATOR_HEADERS).json()
        assert member["elevated"] is False
        assert fc["elevated"] is True
        # The anonymous distribution is present for everyone.
        assert "target_switch_s" in member["distributions"]
