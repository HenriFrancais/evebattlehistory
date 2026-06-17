"""Demo ESI client that serves killmails from data_demo/killmails/ fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from app.observability.logging import log


class DemoEsiClient:
    def __init__(self, demo_data_dir: Path) -> None:
        self._dir = demo_data_dir

    async def fetch_killmail(self, km_id: int, km_hash: str) -> dict[str, object]:
        path = self._dir / "killmails" / f"km_{km_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Demo killmail not found: {path}")
        return json.loads(path.read_text())  # type: ignore[no-any-return]

    async def fetch_killmails(
        self, refs: list[tuple[int, str]]
    ) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        for km_id, km_hash in refs:
            try:
                km = await self.fetch_killmail(km_id, km_hash)
                results.append(km)
            except Exception as exc:
                log.warning("demo_esi.fetch_failed", km_id=km_id, error=str(exc))
        return results

    async def resolve_names(self, ids: list[int]) -> dict[int, dict[str, str]]:
        names_path = self._dir / "names.json"
        if not names_path.exists():
            return {}
        all_names: dict[str, dict[str, str]] = json.loads(names_path.read_text())
        id_set = set(ids)
        return {int(k): v for k, v in all_names.items() if int(k) in id_set}
