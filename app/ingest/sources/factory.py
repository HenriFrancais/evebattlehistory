"""Dispatch URL → BrSource resolver, with demo mode shortcut."""

from __future__ import annotations

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


def get_source(url: str, settings: Settings) -> BrSource:
    if settings.data_source == "demo":
        return DemoSource(settings.demo_data_dir)
    host = urlparse(url).netloc
    if "zkillboard.com" in host:
        return ZkbSource()
    if "evetools.org" in host:
        return AuroraSource()
    raise ValueError(f"Unknown BR source URL: {url}")
