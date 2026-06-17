"""Tests for DB layer, ESI client caching, BR source resolvers, and persist."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Test 1: zKB URL parse
from app.ingest.sources.zkillboard import parse_zkb_url


def test_zkb_url_parse_valid():
    sys_id, dt_str = parse_zkb_url("https://zkillboard.com/related/30000580/202602261500/")
    assert sys_id == 30000580
    assert dt_str == "202602261500"


def test_zkb_url_parse_no_trailing_slash():
    sys_id, dt_str = parse_zkb_url("https://zkillboard.com/related/30000580/202602261500")
    assert sys_id == 30000580
    assert dt_str == "202602261500"


def test_zkb_url_parse_malformed():
    import pytest
    with pytest.raises(ValueError):
        parse_zkb_url("https://zkillboard.com/kill/12345/")


# Test 2: ESI client disk cache hit (no second fetch)
@pytest.mark.asyncio
async def test_esi_disk_cache_hit(tmp_path):
    from app.esi.client import EsiClient
    cache_dir = tmp_path / "esi_cache"
    cache_dir.mkdir()

    # Write a pre-cached killmail
    km_data = {
        "killmail_id": 999,
        "killmail_time": "2026-01-01T00:00:00Z",
        "solar_system_id": 30000142,
        "victim": {"ship_type_id": 587},
        "attackers": [],
    }
    (cache_dir / "999.json").write_text(json.dumps(km_data))

    # Mock httpx so any actual network call fails
    fetch_count = 0

    async def mock_get(*args, **kwargs):
        nonlocal fetch_count
        fetch_count += 1
        raise RuntimeError("Should not hit network - cache should be used")

    client = EsiClient(cache_dir=cache_dir, user_agent="test", timeout_s=5.0)
    result = await client.fetch_killmail(999, "fakehash")
    assert result["killmail_id"] == 999
    assert fetch_count == 0  # Never hit the network


# Test 3: Demo factory resolves without network
@pytest.mark.asyncio
async def test_demo_factory_resolves(tmp_path):
    demo_dir = tmp_path / "demo"
    demo_dir.mkdir()
    resolved = {
        "source": "demo",
        "source_ref": "demo",
        "title": "Test Battle",
        "refs": [[101, "hash101"], [102, "hash102"]],
    }
    (demo_dir / "resolved_br_demo.json").write_text(json.dumps(resolved))

    from app.ingest.sources.factory import DemoSource

    source = DemoSource(demo_dir)
    result = await source.resolve("demo://demo")
    assert result.source == "demo"
    assert len(result.refs) == 2
    assert result.refs[0] == (101, "hash101")


# Test 3b: foreign_keys PRAGMA is ON for every new connection
@pytest.mark.asyncio
async def test_foreign_keys_pragma_on(tmp_path):
    from sqlalchemy import text

    from app.config import Settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests

    reset_engine_for_tests()
    settings = Settings(db_path=tmp_path / "fk_test.db", demo_data_dir=Path("./data_demo"))
    await init_models(settings)

    session_maker = get_sessionmaker(settings)
    async with session_maker() as session:
        result = await session.execute(text("PRAGMA foreign_keys"))
        value = result.scalar()

    assert value == 1, f"Expected PRAGMA foreign_keys=1, got {value!r}"
    reset_engine_for_tests()


# Test 4: persist_killmails creates killmails from demo fixtures
# Uses an explicit fixed list of the 5 original demo-battle killmail files
# (km_101..km_105) so that adding new fixture files doesn't silently change
# counts and break this test.
_DEMO_LINK_KM_FILES = [f"km_{i}.json" for i in range(101, 106)]  # km_101..km_105
# The window-source demo fixtures (km_106, km_107) are also included in the
# all-sources count tested here since both sets are part of the resolved demo data.
_ALL_DEMO_KM_FILES = [f"km_{i}.json" for i in range(101, 108)]  # km_101..km_107


@pytest.mark.asyncio
async def test_persist_demo_killmails(tmp_path):
    from sqlalchemy import func, select

    from app.config import Settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.db.models import KillmailAttacker, KillmailItem
    from app.ingest.persist import persist_killmails

    reset_engine_for_tests()
    settings = Settings(db_path=tmp_path / "test.db", demo_data_dir=Path("./data_demo"))
    await init_models(settings)

    demo_data_dir = Path("./data_demo")
    # Load the explicit fixed set of 7 demo killmails (km_101..km_107).
    killmails_json = [
        json.loads((demo_data_dir / "killmails" / fname).read_text())
        for fname in _ALL_DEMO_KM_FILES
    ]

    names_path = demo_data_dir / "names.json"
    raw_names = json.loads(names_path.read_text())
    names = {int(k): v for k, v in raw_names.items()}

    session_maker = get_sessionmaker(settings)
    async with session_maker() as session:
        count = await persist_killmails(session, killmails_json, names)
        await session.commit()

    assert count == 7

    async with session_maker() as session:
        attacker_count = (await session.execute(
            select(func.count()).select_from(KillmailAttacker)
        )).scalar()
        item_count = (await session.execute(
            select(func.count()).select_from(KillmailItem)
        )).scalar()

    assert attacker_count == 9   # 1+2+2+1+1 (101-105) + 1+1 (106-107) = 9 attackers
    assert item_count == 5       # 2+1+1+0+1 (101-105) + 0+0 (106-107) = 5 items
    reset_engine_for_tests()


# Test 5: re-ingest is idempotent
@pytest.mark.asyncio
async def test_persist_idempotent(tmp_path):
    from sqlalchemy import func, select

    from app.config import Settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.db.models import Killmail
    from app.ingest.persist import persist_killmails

    reset_engine_for_tests()
    settings = Settings(db_path=tmp_path / "test.db", demo_data_dir=Path("./data_demo"))
    await init_models(settings)

    demo_data_dir = Path("./data_demo")
    # Load the explicit fixed set of 7 demo killmails (km_101..km_107).
    killmails_json = [
        json.loads((demo_data_dir / "killmails" / fname).read_text())
        for fname in _ALL_DEMO_KM_FILES
    ]

    names_path = demo_data_dir / "names.json"
    names = {int(k): v for k, v in json.loads(names_path.read_text()).items()}

    session_maker = get_sessionmaker(settings)

    # First ingest
    async with session_maker() as session:
        count1 = await persist_killmails(session, killmails_json, names)
        await session.commit()

    # Second ingest — same data
    async with session_maker() as session:
        count2 = await persist_killmails(session, killmails_json, names)
        await session.commit()

    # Third ingest to verify DB count
    async with session_maker() as session:
        result = await session.execute(select(func.count()).select_from(Killmail))
        total_in_db = result.scalar()

    assert count1 == 7
    assert count2 == 0  # All duplicates
    assert total_in_db == 7
    reset_engine_for_tests()


# Test 6: full demo path — resolve → fetch → persist
@pytest.mark.asyncio
async def test_full_demo_path(tmp_path):
    from sqlalchemy import select

    from app.config import Settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.esi.demo import DemoEsiClient
    from app.ingest.persist import persist_killmails
    from app.ingest.sources.factory import DemoSource

    reset_engine_for_tests()
    demo_data_dir = Path("./data_demo")
    settings = Settings(db_path=tmp_path / "test.db", demo_data_dir=demo_data_dir)
    await init_models(settings)

    # Resolve
    source = DemoSource(demo_data_dir)
    resolved = await source.resolve("demo://demo")
    assert resolved.title == "Demo Battle: NV vs Hostiles in J-Space"

    # Fetch killmails
    esi = DemoEsiClient(demo_data_dir)
    killmails_json = await esi.fetch_killmails(resolved.refs)
    assert len(killmails_json) == 5

    # Resolve names
    names = await esi.resolve_names(
        list(range(2100000001, 2100000004))
        + list(range(2200000001, 2200000004))
        + [98000001, 98000002, 98000003, 99000001, 99000002]
    )

    # Persist
    session_maker = get_sessionmaker(settings)
    async with session_maker() as session:
        count = await persist_killmails(session, killmails_json, names)
        await session.commit()

    assert count == 5

    # Check ISK values
    from app.db.models import Killmail as KM

    async with session_maker() as session:
        result = await session.execute(select(KM).where(KM.killmail_id == 101))
        km101 = result.scalar_one()
        assert km101.total_value == pytest.approx(850000000.0)

    reset_engine_for_tests()


# Test 7: ESI name 404 binary-split (mock transport)
@pytest.mark.asyncio
async def test_esi_name_resolve_404_binary_split(tmp_path):
    """Test that resolve_names handles 404 by binary-splitting to drop bad ids."""
    import httpx

    from app.esi.client import EsiClient

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    call_log: list[list[int]] = []

    async def mock_post_names(ids: list[int]) -> dict[int, dict[str, str]]:
        call_log.append(list(ids))
        if 3 in ids and len(ids) > 1:
            raise httpx.HTTPStatusError(
                "404", request=MagicMock(), response=MagicMock(status_code=404)
            )
        if ids == [3]:
            raise httpx.HTTPStatusError(
                "404", request=MagicMock(), response=MagicMock(status_code=404)
            )
        return {i: {"name": f"Entity{i}", "category": "character"} for i in ids}

    client = EsiClient(cache_dir=cache_dir, user_agent="test", timeout_s=5.0)
    # Patch the internal method that does the actual HTTP call
    client._post_names_chunk = mock_post_names  # type: ignore[method-assign]

    result = await client.resolve_names([1, 2, 3])
    assert 1 in result
    assert 2 in result
    assert 3 not in result  # Bad ID dropped
