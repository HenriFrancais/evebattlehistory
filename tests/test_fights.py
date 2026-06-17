"""Tests for fight clustering, side detection, labelling, outcome computation,
and BR-level aggregation. All pure-function tests need no DB; aggregate tests
use an in-memory SQLite via the engine helpers from Task 1.1.

Demo fixture entity IDs:
  Alliance 99000001 = NV (us / friendly)   — victims in km_101, km_102
  Alliance 99000002 = hostile               — victims in km_103, km_104, km_105
  All 5 kills are in solar_system_id 31002222, timestamps 20:15..20:40.

ISK:
  99000001 lost: 850M + 650M = 1,500M
  99000002 lost: 120M + 50M + 180M  = 350M
  isk_efficiency = 350 / (350 + 1500) ≈ 0.189 → loss
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Helpers / stubs used across multiple test groups
# ---------------------------------------------------------------------------

_BASE = dt.datetime(2026, 6, 10, 20, 0, 0, tzinfo=dt.UTC)


def _t(minutes: int) -> dt.datetime:
    return _BASE + dt.timedelta(minutes=minutes)


@dataclass
class _Attacker:
    character_id: int | None = None
    corporation_id: int | None = None
    alliance_id: int | None = None
    ship_type_id: int | None = None
    damage_done: int = 0
    final_blow: bool = False


@dataclass
class _Kill:
    killmail_id: int
    solar_system_id: int
    killmail_time: dt.datetime
    victim_character_id: int | None = None
    victim_alliance_id: int | None = None
    victim_corporation_id: int | None = None
    victim_ship_type_id: int = 587
    total_value: float | None = None
    attackers: list[_Attacker] = field(default_factory=list)


# ===========================================================================
# 1. cluster_kills
# ===========================================================================


class TestClusterKills:
    def test_same_system_within_window_is_one_fight(self):
        from app.fights.cluster import cluster_kills

        kills = [
            _Kill(1, 30000001, _t(0)),
            _Kill(2, 30000001, _t(10)),   # 10 min gap — within 12-min window
            _Kill(3, 30000001, _t(18)),   # 8 min gap
        ]
        result = cluster_kills(kills)
        assert len(result) == 1
        assert sorted(result[0].killmail_ids) == [1, 2, 3]

    def test_gap_larger_than_window_creates_two_fights(self):
        from app.fights.cluster import cluster_kills

        kills = [
            _Kill(1, 30000001, _t(0)),
            _Kill(2, 30000001, _t(15)),  # 15-min gap > 12-min window → split
        ]
        result = cluster_kills(kills, window_minutes=12)
        assert len(result) == 2

    def test_different_systems_create_separate_fights(self):
        from app.fights.cluster import cluster_kills

        kills = [
            _Kill(1, 30000001, _t(0)),
            _Kill(2, 30000002, _t(1)),   # same time, different system
        ]
        result = cluster_kills(kills)
        assert len(result) == 2
        systems = {f.kills[0].solar_system_id for f in result}
        assert systems == {30000001, 30000002}

    def test_single_kill_is_one_fight(self):
        from app.fights.cluster import cluster_kills

        kills = [_Kill(42, 30000001, _t(0))]
        result = cluster_kills(kills)
        assert len(result) == 1
        assert result[0].killmail_ids == [42]

    def test_empty_input_returns_empty(self):
        from app.fights.cluster import cluster_kills

        assert cluster_kills([]) == []

    def test_window_boundary_exact_is_same_fight(self):
        """A gap equal to window_minutes is within window (<=)."""
        from app.fights.cluster import cluster_kills

        kills = [
            _Kill(1, 30000001, _t(0)),
            _Kill(2, 30000001, _t(12)),   # exactly 12 min → same fight
        ]
        result = cluster_kills(kills, window_minutes=12)
        assert len(result) == 1

    def test_max_duration_splits_long_fight(self):
        """A chain that spans longer than max_duration_minutes is split."""
        from app.fights.cluster import cluster_kills

        # 20 kills, 5 minutes apart → 95 minutes total → exceeds max_duration=60
        kills = [_Kill(i, 30000001, _t(i * 5)) for i in range(20)]
        result = cluster_kills(kills, window_minutes=10, max_duration_minutes=60)
        # Should produce at least 2 fights
        assert len(result) >= 2
        # Every fight must span ≤ 60 minutes
        for f in result:
            times = [k.killmail_time for k in f.kills]
            span = (max(times) - min(times)).total_seconds() / 60
            assert span <= 60


# ===========================================================================
# 2. assign_sides
# ===========================================================================


class TestAssignSides:
    def _simple_fight(self) -> list[_Kill]:
        """Two kills: A attacks B (kill_1), B attacks A (kill_2)."""
        return [
            _Kill(
                killmail_id=1,
                solar_system_id=30000001,
                killmail_time=_t(0),
                victim_alliance_id=10,
                total_value=100.0,
                attackers=[_Attacker(alliance_id=20)],
            ),
            _Kill(
                killmail_id=2,
                solar_system_id=30000001,
                killmail_time=_t(5),
                victim_alliance_id=20,
                total_value=100.0,
                attackers=[_Attacker(alliance_id=10)],
            ),
        ]

    def test_two_opposing_alliances_get_different_sides(self):
        from app.fights.sides import assign_sides

        result = assign_sides(self._simple_fight())
        # side_map: alliance_id → side_idx
        sides = {aid: s for aid, s in result.alliance_sides.items()}
        assert sides[10] != sides[20]

    def test_co_attackers_same_side(self):
        """Alliances 30 and 31 both attack alliance 10 → must be same side."""
        from app.fights.sides import assign_sides

        kills = [
            _Kill(
                killmail_id=1,
                solar_system_id=30000001,
                killmail_time=_t(0),
                victim_alliance_id=10,
                total_value=200.0,
                attackers=[
                    _Attacker(alliance_id=30),
                    _Attacker(alliance_id=31),
                ],
            )
        ]
        result = assign_sides(kills)
        sides = result.alliance_sides
        assert sides.get(30) == sides.get(31)

    def test_npc_only_attackers_no_crash(self):
        """Attackers with alliance_id=None should not crash."""
        from app.fights.sides import assign_sides

        kills = [
            _Kill(
                killmail_id=1,
                solar_system_id=30000001,
                killmail_time=_t(0),
                victim_alliance_id=10,
                total_value=50.0,
                attackers=[_Attacker(alliance_id=None)],
            )
        ]
        result = assign_sides(kills)
        # Should not raise; alliance 10 (victim with no attacker alliance) present
        assert isinstance(result.alliance_sides, dict)


# ===========================================================================
# 3. label_sides
# ===========================================================================


class TestLabelSides:
    """Pure function; no DB needed."""

    def test_our_alliance_is_friendly(self):
        from app.fights.labelling import label_sides

        per_side = {
            0: {"alliance_ids": {99000001}, "corp_ids": set()},
            1: {"alliance_ids": {99000002}, "corp_ids": set()},
        }
        labels = label_sides(per_side, our_alliance_ids={99000001}, our_corp_ids=set())
        assert labels[0] == "friendly"

    def test_opposing_side_is_hostile(self):
        from app.fights.labelling import label_sides

        per_side = {
            0: {"alliance_ids": {99000001}, "corp_ids": set()},
            1: {"alliance_ids": {99000002}, "corp_ids": set()},
        }
        labels = label_sides(per_side, our_alliance_ids={99000001}, our_corp_ids=set())
        assert labels[1] == "hostile"

    def test_unrelated_side_is_neutral(self):
        """Side 2 (88888888) did not fight side 0 (us) → neutral."""
        from app.fights.labelling import label_sides

        per_side = {
            0: {"alliance_ids": {99000001}, "corp_ids": set()},
            1: {"alliance_ids": {99000002}, "corp_ids": set()},
            2: {"alliance_ids": {88888888}, "corp_ids": set()},
        }
        # Only sides 0 and 1 directly fought each other
        labels = label_sides(
            per_side,
            our_alliance_ids={99000001},
            our_corp_ids=set(),
            opposing_sides={(0, 1)},
        )
        assert labels[2] == "neutral"

    def test_corp_match_is_friendly(self):
        from app.fights.labelling import label_sides

        per_side = {
            0: {"alliance_ids": set(), "corp_ids": {98000001}},
            1: {"alliance_ids": {99000002}, "corp_ids": set()},
        }
        labels = label_sides(
            per_side, our_alliance_ids=set(), our_corp_ids={98000001}
        )
        assert labels[0] == "friendly"
        assert labels[1] == "hostile"

    def test_no_friendly_side_all_neutral(self):
        from app.fights.labelling import label_sides

        per_side = {
            0: {"alliance_ids": {11}, "corp_ids": set()},
            1: {"alliance_ids": {22}, "corp_ids": set()},
        }
        labels = label_sides(
            per_side, our_alliance_ids={99000001}, our_corp_ids=set()
        )
        # Neither side matches us → both neutral
        assert labels[0] == "neutral"
        assert labels[1] == "neutral"


# ===========================================================================
# 4. compute_outcome + ISK efficiency thresholds
# ===========================================================================


class TestComputeOutcome:
    """Tests for the per-side ISK outcome and the BR-level efficiency thresholds.

    Win metric is OUR ISK efficiency = our_destroyed / (our_destroyed + our_lost)
    where our_destroyed = hostile isk_lost, our_lost = friendly isk_lost.
    """

    def test_basic_win_loss_assignment(self):
        from app.fights.outcomes import compute_outcome

        # Side 0 (us) lost 100, Side 1 (them) lost 500
        # our efficiency = 500 / (500 + 100) ≈ 0.833 → win
        sides_isk = {0: 100.0, 1: 500.0}
        result = compute_outcome(sides_isk)
        assert result[0]["isk_lost"] == 100.0
        assert result[1]["isk_lost"] == 500.0

    def test_zero_isk_returns_none_efficiency(self):
        from app.fights.outcomes import compute_outcome

        result = compute_outcome({0: 0.0, 1: 0.0})
        assert result[0]["efficiency"] is None

    # --- BR-level win/tie/loss threshold table tests ---

    @pytest.mark.parametrize(
        "our_destroyed, our_lost, expected_result",
        [
            # Win boundary: eff = 0.60 exactly → win
            (60.0, 40.0, "win"),
            # Win above boundary
            (70.0, 30.0, "win"),
            # Tie: eff = 0.40 exactly → tie
            (40.0, 60.0, "tie"),
            # Tie: eff = 0.5999 → tie (just below win boundary)
            (59.99, 40.01, "tie"),
            # Tie: middle of range
            (50.0, 50.0, "tie"),
            # Loss: eff < 0.40
            (30.0, 70.0, "loss"),
            # Loss: eff = 0.0 (nothing destroyed)
            (0.0, 100.0, "loss"),
        ],
    )
    def test_br_result_thresholds(
        self, our_destroyed: float, our_lost: float, expected_result: str
    ):
        from app.fights.outcomes import classify_br_result

        result = classify_br_result(our_destroyed, our_lost)
        assert result == expected_result

    def test_br_result_none_when_no_activity(self):
        from app.fights.outcomes import classify_br_result

        assert classify_br_result(0.0, 0.0) is None


# ===========================================================================
# 4b. compute_fight_sides — corp_ids, character_ids, pilot_count (I1, M1, M3)
# ===========================================================================


class TestComputeFightSides:
    """Unit tests for compute_fight_sides: corp_ids population (I1),
    pilot_count from character_ids (M1), and ship count deduplication (M3)."""

    def _make_kills(self) -> list[_Kill]:
        """Two kills: side-0 loses, side-1 attacks.

        Side 0 (victim alliance 10, corp 1000, char 2000):
          - km 1: victim char 2000, corp 1000, alliance 10, ship 587
          - km 2: victim char 2001, corp 1000, alliance 10, ship 587

        Side 1 (attacker alliance 20, corp 3000):
          - char 4000 attacks both kills on ship 11176 (Vagabond)
          - char 4001 attacks km 1 on ship 11176
        """
        return [
            _Kill(
                killmail_id=1,
                solar_system_id=30000001,
                killmail_time=_t(0),
                victim_character_id=2000,
                victim_corporation_id=1000,
                victim_alliance_id=10,
                victim_ship_type_id=587,
                total_value=100.0,
                attackers=[
                    _Attacker(
                        character_id=4000, corporation_id=3000, alliance_id=20, ship_type_id=11176
                    ),
                    _Attacker(
                        character_id=4001, corporation_id=3000, alliance_id=20, ship_type_id=11176
                    ),
                ],
            ),
            _Kill(
                killmail_id=2,
                solar_system_id=30000001,
                killmail_time=_t(5),
                victim_character_id=2001,
                victim_corporation_id=1000,
                victim_alliance_id=10,
                victim_ship_type_id=587,
                total_value=100.0,
                attackers=[
                    # char 4000 again — should NOT be double-counted
                    _Attacker(
                        character_id=4000, corporation_id=3000, alliance_id=20, ship_type_id=11176
                    ),
                ],
            ),
        ]

    def test_corp_ids_populated_for_victims(self) -> None:
        """I1: victim corp ids are added to the victim side's corp_ids."""
        from app.fights.outcomes import compute_fight_sides

        kills = self._make_kills()
        side_for_alliance = {10: 0, 20: 1}
        per_side = compute_fight_sides(kills, side_for_alliance)

        assert 1000 in per_side[0].corp_ids, "victim corp 1000 must be in side 0 corp_ids"

    def test_corp_ids_populated_for_attackers(self) -> None:
        """I1: attacker corp ids are added to the attacker side's corp_ids."""
        from app.fights.outcomes import compute_fight_sides

        kills = self._make_kills()
        side_for_alliance = {10: 0, 20: 1}
        per_side = compute_fight_sides(kills, side_for_alliance)

        assert 3000 in per_side[1].corp_ids, "attacker corp 3000 must be in side 1 corp_ids"

    def test_pilot_count_counts_distinct_characters(self) -> None:
        """M1: pilot_count is distinct character count, not alliance count."""
        from app.fights.outcomes import compute_fight_sides

        kills = self._make_kills()
        side_for_alliance = {10: 0, 20: 1}
        per_side = compute_fight_sides(kills, side_for_alliance)

        # Side 0: chars 2000 and 2001 (2 victims)
        assert per_side[0].pilot_count == 2
        # Side 1: chars 4000 and 4001 (char 4000 appears twice but only counted once)
        assert per_side[1].pilot_count == 2

    def test_ship_counts_deduplicated_by_character(self) -> None:
        """M3: an attacker appearing in N kills is counted once for their ship."""
        from app.fights.outcomes import compute_fight_sides

        kills = self._make_kills()
        side_for_alliance = {10: 0, 20: 1}
        per_side = compute_fight_sides(kills, side_for_alliance)

        # Side 1 has chars 4000 + 4001, both on ship 11176.
        # Even though char 4000 appears as attacker in 2 kills, ship 11176 count
        # should be 2 (one per distinct pilot), not 3.
        assert per_side[1].ship_counts.get(11176) == 2, (
            f"Expected 2 distinct pilots on ship 11176, got {per_side[1].ship_counts.get(11176)}"
        )


class TestCorpOnlyLabellingAndBrResult:
    """I1 + M1 integration: a corp-only side (no alliance_id) must label correctly
    and produce a non-None BR result."""

    def test_corp_only_side_labels_friendly_and_produces_br_result(self) -> None:
        """Side with only corp_ids (no alliance_id) must be labelled 'friendly',
        the opposing side 'hostile', and classify_br_result must return non-None."""
        from app.fights.labelling import label_sides
        from app.fights.outcomes import classify_br_result

        our_corp_id = 98000001
        hostile_alliance_id = 99000002

        per_side = {
            0: {"alliance_ids": set(), "corp_ids": {our_corp_id}},
            1: {"alliance_ids": {hostile_alliance_id}, "corp_ids": set()},
        }
        labels = label_sides(
            per_side,
            our_alliance_ids=set(),
            our_corp_ids={our_corp_id},
        )

        assert labels[0] == "friendly", f"corp-only side must be 'friendly', got {labels[0]}"
        assert labels[1] == "hostile", f"opposing side must be 'hostile', got {labels[1]}"

        # BR result must be non-None when there is activity
        br_result = classify_br_result(our_destroyed=500.0, our_lost=100.0)
        assert br_result is not None
        assert br_result == "win"

    def test_compute_fight_sides_corp_only_victim_contributes_corp(self) -> None:
        """I1: a victim with no alliance_id but with corp_id has corp tracked."""
        from app.fights.outcomes import compute_fight_sides

        # Kill where victim has no alliance, only corp
        kills = [
            _Kill(
                killmail_id=10,
                solar_system_id=30000001,
                killmail_time=_t(0),
                victim_character_id=5001,
                victim_corporation_id=98000001,
                victim_alliance_id=None,  # no alliance
                victim_ship_type_id=587,
                total_value=50.0,
                attackers=[
                    _Attacker(
                        character_id=6001,
                        corporation_id=99000002,
                        alliance_id=200,
                        ship_type_id=11176,
                    ),
                ],
            )
        ]
        # victim has no alliance_id so side lookup returns 0
        side_for_alliance = {200: 1}
        per_side = compute_fight_sides(kills, side_for_alliance)

        # Side 0 (victim): corp 98000001 must be tracked
        assert 98000001 in per_side[0].corp_ids, (
            "victim corp must be in corp_ids even with no alliance"
        )
        # Side 0 pilot_count = 1 (char 5001)
        assert per_side[0].pilot_count == 1


# ===========================================================================
# 5. aggregate_br (DB-aware orchestrator)
# ===========================================================================


async def _setup_demo_db(tmp_path: Path) -> Any:
    """Load demo fixtures into a test DB; return session_maker."""
    import datetime as dt

    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from app.config import Settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.db.models import BattleReport, BrKillmail
    from app.esi.demo import DemoEsiClient
    from app.ingest.persist import persist_killmails
    from app.ingest.sources.factory import DemoSource

    reset_engine_for_tests()
    demo_data_dir = Path("./data_demo")
    settings = Settings(db_path=tmp_path / "test.db", demo_data_dir=demo_data_dir)
    await init_models(settings)

    source = DemoSource(demo_data_dir)
    resolved = await source.resolve("demo://demo")

    esi = DemoEsiClient(demo_data_dir)
    killmails_json = await esi.fetch_killmails(resolved.refs)
    names = await esi.resolve_names(
        list(range(2100000001, 2100000004))
        + list(range(2200000001, 2200000004))
        + [98000001, 98000002, 98000003, 99000001, 99000002]
    )

    session_maker = get_sessionmaker(settings)
    async with session_maker() as session:
        # Create BR record
        br = BattleReport(
            br_id="demo-br-001",
            source="demo",
            source_url="demo://demo",
            source_ref="demo",
            title="Demo Battle: NV vs Hostiles in J-Space",
            created_by_user="test",
            status="done",
            created_at=dt.datetime.now(dt.UTC),
            km_count=len(killmails_json),
        )
        session.add(br)
        await session.flush()

        # Persist killmails
        await persist_killmails(session, killmails_json, names)
        await session.flush()

        # Link killmails to BR
        km_ids = [km["killmail_id"] for km in killmails_json]
        brkm_rows = [{"br_id": "demo-br-001", "killmail_id": kid} for kid in km_ids]
        await session.execute(sqlite_insert(BrKillmail).values(brkm_rows))
        await session.commit()

    return session_maker


@pytest.mark.asyncio
async def test_aggregate_br_fight_count(tmp_path):
    """All 5 demo kills are in same system within 25 min → 1 fight."""
    from sqlalchemy import select

    from app.db.models import BattleReport
    from app.fights.aggregate import aggregate_br

    session_maker = await _setup_demo_db(tmp_path)

    async with session_maker() as session:
        await aggregate_br(
            session,
            br_id="demo-br-001",
            our_alliance_ids=[99000001],
            our_corp_ids=[],
        )
        await session.commit()

    async with session_maker() as session:
        br = (
            await session.execute(
                select(BattleReport).where(BattleReport.br_id == "demo-br-001")
            )
        ).scalar_one()
        assert br.fight_count == 1


@pytest.mark.asyncio
async def test_aggregate_br_isk_and_result(tmp_path):
    """Demo: NV lost 1500M, hostiles lost 350M → eff≈0.189 → loss."""
    from sqlalchemy import select

    from app.db.models import BattleReport
    from app.fights.aggregate import aggregate_br

    session_maker = await _setup_demo_db(tmp_path)

    async with session_maker() as session:
        await aggregate_br(
            session,
            br_id="demo-br-001",
            our_alliance_ids=[99000001],
            our_corp_ids=[],
        )
        await session.commit()

    async with session_maker() as session:
        br = (
            await session.execute(
                select(BattleReport).where(BattleReport.br_id == "demo-br-001")
            )
        ).scalar_one()
        assert br.our_isk_lost == pytest.approx(1_500_000_000.0, rel=1e-3)
        assert br.our_isk_destroyed == pytest.approx(350_000_000.0, rel=1e-3)
        assert br.isk_efficiency == pytest.approx(
            350_000_000.0 / (350_000_000.0 + 1_500_000_000.0), rel=1e-3
        )
        assert br.result == "loss"


@pytest.mark.asyncio
async def test_aggregate_br_ship_counts_populated(tmp_path):
    """fight_ship_counts and br_ship_counts must have rows after aggregation."""
    from sqlalchemy import func, select

    from app.db.models import BrShipCount, FightShipCount
    from app.fights.aggregate import aggregate_br

    session_maker = await _setup_demo_db(tmp_path)

    async with session_maker() as session:
        await aggregate_br(
            session,
            br_id="demo-br-001",
            our_alliance_ids=[99000001],
            our_corp_ids=[],
        )
        await session.commit()

    async with session_maker() as session:
        fight_row_count = (
            await session.execute(select(func.count()).select_from(FightShipCount))
        ).scalar()
        br_row_count = (
            await session.execute(select(func.count()).select_from(BrShipCount))
        ).scalar()

    assert fight_row_count > 0, "fight_ship_counts should have rows"
    assert br_row_count > 0, "br_ship_counts should have rows"


@pytest.mark.asyncio
async def test_aggregate_br_idempotent(tmp_path):
    """Running aggregate_br twice on the same BR produces same counts."""
    from sqlalchemy import func, select

    from app.db.models import BattleReport, BrFight, FightKill
    from app.fights.aggregate import aggregate_br

    session_maker = await _setup_demo_db(tmp_path)

    for _ in range(2):
        async with session_maker() as session:
            await aggregate_br(
                session,
                br_id="demo-br-001",
                our_alliance_ids=[99000001],
                our_corp_ids=[],
            )
            await session.commit()

    async with session_maker() as session:
        br = (
            await session.execute(
                select(BattleReport).where(BattleReport.br_id == "demo-br-001")
            )
        ).scalar_one()
        fight_kill_count = (
            await session.execute(select(func.count()).select_from(FightKill))
        ).scalar()
        br_fight_count = (
            await session.execute(select(func.count()).select_from(BrFight))
        ).scalar()

    assert br.fight_count == 1
    assert fight_kill_count == 5   # 5 kills, one per FightKill row
    assert br_fight_count == 1     # One BrFight linking the BR to the fight


@pytest.mark.asyncio
async def test_aggregate_br_battle_at_set(tmp_path):
    """battle_at should equal the earliest killmail timestamp in the demo."""
    from sqlalchemy import select

    from app.db.models import BattleReport
    from app.fights.aggregate import aggregate_br

    session_maker = await _setup_demo_db(tmp_path)

    async with session_maker() as session:
        await aggregate_br(
            session,
            br_id="demo-br-001",
            our_alliance_ids=[99000001],
            our_corp_ids=[],
        )
        await session.commit()

    async with session_maker() as session:
        br = (
            await session.execute(
                select(BattleReport).where(BattleReport.br_id == "demo-br-001")
            )
        ).scalar_one()

    # km_101 is the earliest at 2026-06-10T20:15:00Z
    expected = dt.datetime(2026, 6, 10, 20, 15, 0, tzinfo=dt.UTC)
    # SQLite stores tz-unaware; accept either
    if br.battle_at is not None and br.battle_at.tzinfo is None:
        expected = expected.replace(tzinfo=None)
    assert br.battle_at == expected


@pytest.mark.asyncio
async def test_aggregate_br_rerun_clears_log_orphans(tmp_path):
    """Re-running aggregate_br must not leave LogEvent/LogEventBucket rows pointing
    at deleted fight_ids.

    Sequence:
    1. Ingest demo BR + aggregate → fight_id F1 exists.
    2. Associate a synthetic log file (creates LogEvent.fight_id=F1 + buckets for F1).
    3. Re-run aggregate_br → old Fight F1 deleted, new Fight F2 minted.
    4. Re-associate logs → events stamped to F2, buckets rebuilt for F2.
    5. Assert: no LogEvent.fight_id or LogEventBucket.fight_id references a
       fight_id that does not exist in the Fight table.
    """
    import datetime as dt
    import hashlib
    import uuid

    from sqlalchemy import func, select

    from app.db.models import (
        BrFight,
        Fight,
        GamelogFile,
        LogEvent,
        LogEventBucket,
    )
    from app.fights.aggregate import aggregate_br
    from app.logs.associate import associate_logs_for_br

    session_maker = await _setup_demo_db(tmp_path)

    # 1. First aggregation pass
    async with session_maker() as session:
        await aggregate_br(
            session,
            br_id="demo-br-001",
            our_alliance_ids=[99000001],
            our_corp_ids=[],
        )
        await session.commit()

    # Capture the fight_id produced in the first pass
    async with session_maker() as session:
        first_fight_id_row = (
            await session.execute(
                select(BrFight.fight_id).where(BrFight.br_id == "demo-br-001")
            )
        ).scalar_one()
    first_fight_id: int = int(first_fight_id_row)

    # 2. Insert a synthetic gamelog for a participant character (2100000001 = victim in km_101)
    PARTICIPANT_CHAR = 2100000001
    # Fight window from demo: 20:15-20:40 UTC 2026-06-10
    fight_ts = dt.datetime(2026, 6, 10, 20, 20, 0, tzinfo=dt.UTC)
    log_start = dt.datetime(2026, 6, 10, 20, 10, 0, tzinfo=dt.UTC)
    log_end = dt.datetime(2026, 6, 10, 20, 45, 0, tzinfo=dt.UTC)

    async with session_maker() as session:
        sha = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
        gf = GamelogFile(
            uploaded_by_user="test",
            claimed_character_id=PARTICIPANT_CHAR,
            character_name="TestChar",
            original_filename="test.txt",
            resolved_via="filename",
            log_start_at=log_start,
            log_end_at=log_end,
            stored_path="/tmp/test.txt",
            sha256=sha,
            mime="text/plain",
            size=100,
            parse_status="parsed",
            event_count=1,
            uploaded_at=dt.datetime.now(dt.UTC),
        )
        session.add(gf)
        await session.flush()
        file_id: int = gf.file_id

        session.add(
            LogEvent(
                file_id=file_id,
                character_id=PARTICIPANT_CHAR,
                ts=fight_ts,
                direction="in",
                effect_type="scram",
                amount=None,
                fight_id=None,
            )
        )
        await session.commit()

    # Associate the log file; events should get fight_id=first_fight_id
    async with session_maker() as session:
        await associate_logs_for_br(session, "demo-br-001")
        await session.commit()

    # Verify events and buckets reference the first fight
    async with session_maker() as session:
        stamped_count = (
            await session.execute(
                select(func.count())
                .select_from(LogEvent)
                .where(LogEvent.fight_id == first_fight_id)
            )
        ).scalar_one()
        bucket_count = (
            await session.execute(
                select(func.count())
                .select_from(LogEventBucket)
                .where(LogEventBucket.fight_id == first_fight_id)
            )
        ).scalar_one()
    assert stamped_count >= 1, "Events should be stamped to first_fight_id before re-aggregate"
    assert bucket_count >= 1, "Buckets should exist for first_fight_id before re-aggregate"

    # 3. Re-run aggregate_br (simulates sweep_pending re-running interrupted ingest).
    #    _clear_derived_rows should null out LogEvent.fight_id and delete
    #    LogEventBucket rows BEFORE deleting Fight rows.
    async with session_maker() as session:
        await aggregate_br(
            session,
            br_id="demo-br-001",
            our_alliance_ids=[99000001],
            our_corp_ids=[],
        )
        await session.commit()

    # Immediately after re-aggregate (before re-association): LogEvent.fight_id
    # must be NULL (cleared by _clear_derived_rows) and no LogEventBucket rows
    # should reference the old fight_id — even if SQLite recycled that integer.
    async with session_maker() as session:
        # All events belonging to our gamelog file must have fight_id=NULL now
        events_still_stamped = (
            await session.execute(
                select(func.count())
                .select_from(LogEvent)
                .where(LogEvent.file_id == file_id)
                .where(LogEvent.fight_id.is_not(None))
            )
        ).scalar_one()
        bucket_total = (
            await session.execute(
                select(func.count()).select_from(LogEventBucket)
            )
        ).scalar_one()
    assert events_still_stamped == 0, (
        "_clear_derived_rows must null LogEvent.fight_id before deleting Fight rows"
    )
    assert bucket_total == 0, (
        "_clear_derived_rows must delete LogEventBucket rows before deleting Fight rows"
    )

    # 4. Re-associate logs → events re-stamped to new fight_id, buckets rebuilt.
    async with session_maker() as session:
        await associate_logs_for_br(session, "demo-br-001")
        await session.commit()

    # 5. Final integrity check: every non-null LogEvent.fight_id and every
    #    LogEventBucket.fight_id must reference an existing Fight row.
    async with session_maker() as session:
        all_fight_ids_result = await session.execute(select(Fight.fight_id))
        all_fight_ids: set[int] = set(all_fight_ids_result.scalars())

        orphaned_event_fids_result = await session.execute(
            select(LogEvent.fight_id)
            .where(LogEvent.fight_id.is_not(None))
            .distinct()
        )
        orphaned_event_fids = {
            fid for fid in orphaned_event_fids_result.scalars()
            if fid is not None and int(fid) not in all_fight_ids
        }

        orphaned_bucket_fids_result = await session.execute(
            select(LogEventBucket.fight_id).distinct()
        )
        orphaned_bucket_fids = {
            fid for fid in orphaned_bucket_fids_result.scalars()
            if fid is not None and int(fid) not in all_fight_ids
        }

    assert not orphaned_event_fids, (
        f"LogEvent rows reference deleted fight_ids: {orphaned_event_fids}"
    )
    assert not orphaned_bucket_fids, (
        f"LogEventBucket rows reference deleted fight_ids: {orphaned_bucket_fids}"
    )
    # Events must be re-stamped to the surviving fight
    async with session_maker() as session:
        re_stamped = (
            await session.execute(
                select(func.count())
                .select_from(LogEvent)
                .where(LogEvent.file_id == file_id)
                .where(LogEvent.fight_id.is_not(None))
            )
        ).scalar_one()
    assert re_stamped >= 1, "associate_logs_for_br must re-stamp events after re-aggregation"
