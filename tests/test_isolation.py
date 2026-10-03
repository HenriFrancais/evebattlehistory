"""The test suite must never read or write the developer's real runtime data
(.env → var/db/dev.db, var/logs) or talk to real upstreams with real tokens."""

from __future__ import annotations

from pathlib import Path

REPO_VAR = (Path(__file__).resolve().parent.parent / "var").resolve()


def _under_repo_var(p: Path) -> bool:
    return REPO_VAR in p.resolve().parents or p.resolve() == REPO_VAR


def test_default_settings_point_at_temp_dirs_not_the_repo() -> None:
    from app.config import get_settings

    s = get_settings()
    for path in (s.db_path, s.log_dir, s.esi_cache_dir):
        assert not _under_repo_var(path), f"{path} is the developer's real data"


def test_default_settings_use_no_real_credentials_or_upstreams() -> None:
    from app.config import get_settings

    s = get_settings()
    assert s.data_source == "demo"
    assert s.discord_bot_token == ""
    assert s.nv_api_token == ""
    assert s.backup_rclone_remote == ""
    assert s.dev_mode is False


def test_make_client_default_db_is_isolated(make_client) -> None:  # type: ignore[no-untyped-def]
    from app.config import get_settings

    make_client()
    assert not _under_repo_var(get_settings().db_path)
