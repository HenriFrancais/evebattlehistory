"""Dispatch URL → BrSource resolver, with demo mode shortcut."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from urllib.parse import urlparse

from app.config import Settings
from app.ingest.sources.aurora import AuroraSource
from app.ingest.sources.base import BrSource, BrUnavailable, ResolvedBr
from app.ingest.sources.zkillboard import ZkbSource


class DemoSource:
    """Resolves from data_demo/resolved_br_<ref>.json fixtures."""

    def __init__(self, demo_data_dir: Path) -> None:
        self._dir = demo_data_dir

    async def resolve(self, url: str) -> ResolvedBr:
        ref = url.split("/")[-1] or "demo"
        path = self._dir / f"resolved_br_{ref}.json"
        if not path.exists():
            raise BrUnavailable(f"Demo fixture not found: {path}")
        data: dict[str, object] = json.loads(path.read_text())
        refs_raw = data.get("refs", [])
        if not isinstance(refs_raw, list):
            raise BrUnavailable(
                f"Demo fixture {path} has malformed 'refs': "
                f"expected list, got {type(refs_raw).__name__}"
            )
        return ResolvedBr(
            source=str(data["source"]),
            source_ref=str(data["source_ref"]),
            title=str(data["title"]) if data.get("title") else None,
            refs=[(int(r[0]), str(r[1])) for r in refs_raw],
        )


class DemoWindowSource:
    """Resolves a time-window source from data_demo/resolved_window_demo.json fixture."""

    def __init__(self, demo_data_dir: Path) -> None:
        self._dir = demo_data_dir

    async def resolve_window(
        self,
        system_id: int,
        window_start: dt.datetime,
        window_end: dt.datetime,
    ) -> ResolvedBr:
        # NOTE (demo-only limitation): this implementation ignores system_id,
        # window_start, and window_end entirely — it always returns the fixed
        # demo window fixture (resolved_window_demo.json).  Two demo window
        # sources for different systems or time ranges will resolve to the same
        # killmail set.  Real mode (data_source != "demo") resolves per
        # system + window via zKB paging.
        path = self._dir / "resolved_window_demo.json"
        if not path.exists():
            raise BrUnavailable(f"Demo window fixture not found: {path}")
        data: dict[str, object] = json.loads(path.read_text())
        refs_raw = data.get("refs", [])
        if not isinstance(refs_raw, list):
            raise BrUnavailable(
                f"Demo window fixture {path} has malformed 'refs': "
                f"expected list, got {type(refs_raw).__name__}"
            )
        return ResolvedBr(
            source=str(data["source"]),
            source_ref=str(data.get("source_ref", f"window/{system_id}/demo")),
            title=str(data["title"]) if data.get("title") else None,
            refs=[(int(r[0]), str(r[1])) for r in refs_raw],
        )


def get_source(url: str, settings: Settings) -> BrSource:
    if settings.data_source == "demo":
        return DemoSource(settings.demo_data_dir)
    host = urlparse(url).netloc
    if "zkillboard.com" in host:
        return ZkbSource()
    if "evetools.org" in host:
        return AuroraSource()
    raise ValueError(f"Unknown BR source URL: {url}")


async def resolve_source(
    source_kind: str,
    source_url: str | None,
    source_system_id: int | None,
    source_window_start: dt.datetime | None,
    source_window_end: dt.datetime | None,
    source_label: str | None,
    settings: Settings,
) -> ResolvedBr:
    """Dispatch a BrSource to the appropriate resolver.

    kind=link → existing get_source(url) resolver
    kind=window → zKB system-window paging (or demo fixture offline)
    """
    if source_kind == "link":
        if not source_url:
            raise BrUnavailable("link source has no URL")
        resolver = get_source(source_url, settings)
        return await resolver.resolve(source_url)

    elif source_kind == "window":
        if (
            source_system_id is None
            or source_window_start is None
            or source_window_end is None
        ):
            raise BrUnavailable("window source missing system_id, window_start, or window_end")

        if settings.data_source == "demo":
            demo_resolver = DemoWindowSource(settings.demo_data_dir)
            return await demo_resolver.resolve_window(
                source_system_id, source_window_start, source_window_end
            )

        # Real mode: use zKB system-kills window paging
        import httpx

        from app.ingest.sources.zkillboard import fetch_window_killmails

        async with httpx.AsyncClient(
            headers={"User-Agent": "nv-br"}, timeout=30.0
        ) as client:
            refs, values = await fetch_window_killmails(
                client, source_system_id, source_window_start, source_window_end
            )

        label = source_label or f"window/{source_system_id}"
        return ResolvedBr(
            source="zkb",
            source_ref=(
                f"{source_system_id}/{source_window_start.strftime('%Y%m%d%H%M')}"
            ),
            title=label,
            refs=refs,
            values=values,
        )

    else:
        raise BrUnavailable(f"Unknown source kind: {source_kind!r}")
