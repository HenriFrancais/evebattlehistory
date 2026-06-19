"""zKillboard battle report resolver."""

from __future__ import annotations

import datetime as dt
import re
from urllib.parse import urlparse

import httpx

from app.ingest.sources.base import ResolvedBr
from app.observability.logging import log

ZKB_API = "https://zkillboard.com/api"
_RELATED_RE = re.compile(r"^/related/(\d+)/(\d{12})/?$")


def parse_zkb_url(url: str) -> tuple[int, str]:
    """Parse a zKillboard /related/ URL. Returns (system_id, datetime_str)."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    m = _RELATED_RE.match(path)
    if not m:
        raise ValueError(f"Not a valid zKillboard /related/ URL: {url!r}")
    system_id = int(m.group(1))
    dt_str = m.group(2)
    return system_id, dt_str


def _extract_refs_from_related(
    data: object,
) -> tuple[list[tuple[int, str]], dict[int, float | None]]:
    """Extract (killmail_id, hash) pairs and km_id→totalValue from a /api/related/ response.

    Merges teamA.kills and teamB.kills, skipping any entry lacking a valid zkb.hash.
    Deduplicated (first occurrence wins).
    """
    refs: dict[int, str] = {}
    values: dict[int, float | None] = {}
    if not isinstance(data, dict):
        return [], {}
    summary = data.get("summary")
    if not isinstance(summary, dict):
        return [], {}
    for team_key in ("teamA", "teamB"):
        team = summary.get(team_key)
        if not isinstance(team, dict):
            continue
        kills = team.get("kills")
        if not isinstance(kills, dict):
            continue
        for kill_id_str, kill_obj in kills.items():
            if not isinstance(kill_obj, dict):
                continue
            zkb = kill_obj.get("zkb")
            if not isinstance(zkb, dict):
                continue
            km_hash = zkb.get("hash")
            if not isinstance(km_hash, str) or not km_hash:
                continue
            try:
                km_id = int(kill_id_str)
            except ValueError:
                continue
            if km_id not in refs:
                refs[km_id] = km_hash
                tv = zkb.get("totalValue")
                values[km_id] = float(tv) if isinstance(tv, (int, float)) else None
    return list(refs.items()), values


class ZkbSource:
    """Resolves a zKillboard /related/ URL to a list of killmail refs."""

    async def resolve(self, url: str) -> ResolvedBr:
        system_id, dt_str = parse_zkb_url(url)

        refs: list[tuple[int, str]] = []
        values: dict[int, float | None] = {}
        title: str | None = None
        async with httpx.AsyncClient(
            headers={"User-Agent": "nv-br"}, timeout=30.0
        ) as client:
            api_url = f"{ZKB_API}/related/{system_id}/{dt_str}/"
            resp = await client.get(api_url)
            if resp.status_code == 200:
                data = resp.json()
                refs, values = _extract_refs_from_related(data)
                if isinstance(data, dict):
                    system_name = data.get("systemName")
                    if isinstance(system_name, str) and system_name:
                        title = f"{system_name} {dt_str}"
            else:
                log.warning(
                    "zkb.api_error",
                    status=resp.status_code,
                    system_id=system_id,
                    dt_str=dt_str,
                )
                values = {}

        return ResolvedBr(
            source="zkb",
            source_ref=f"{system_id}/{dt_str}",
            title=title,
            refs=refs,
            values=values,
        )


async def fetch_window_killmails(
    client: httpx.AsyncClient,
    system_id: int,
    window_start: dt.datetime,
    window_end: dt.datetime,
) -> tuple[list[tuple[int, str]], dict[int, float | None]]:
    """Fetch killmail refs for a system near a time window via zKB /related/.

    Uses GET /api/related/{system_id}/{window_start:%Y%m%d%H%M}/ to retrieve
    the battle report closest to that timestamp.  zKill groups kills by
    proximity to the given time (using an internal exHours window), so
    ``window_end`` is advisory in v1 — the endpoint does not support an
    explicit end boundary.  The solarSystemID paging feed is NOT used because
    it does not return killmail_time and therefore cannot be filtered by window.
    """
    api_url = f"{ZKB_API}/related/{system_id}/{window_start:%Y%m%d%H%M}/"
    resp = await client.get(api_url)
    if resp.status_code != 200:
        log.warning(
            "zkb.api_error",
            status=resp.status_code,
            system_id=system_id,
            window_start=window_start.isoformat(),
        )
        return [], {}
    data = resp.json()
    return _extract_refs_from_related(data)
