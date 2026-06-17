"""zKillboard battle report resolver."""

from __future__ import annotations

import datetime as dt
import re
from urllib.parse import urlparse

import httpx

from app.ingest.sources.base import ResolvedBr
from app.observability.logging import log

ZKB_API = "https://zkillboard.com/api"
MAX_PAGES = 5
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


class ZkbSource:
    """Resolves a zKillboard /related/ URL to a list of killmail refs."""

    async def resolve(self, url: str) -> ResolvedBr:
        system_id, dt_str = parse_zkb_url(url)

        # Parse window: YYYYMMDDHHMM → start/end (±1h window)
        window_start = dt.datetime.strptime(dt_str, "%Y%m%d%H%M").replace(
            tzinfo=dt.UTC
        )
        window_end = window_start + dt.timedelta(hours=2)

        refs: list[tuple[int, str]] = []
        async with httpx.AsyncClient(
            headers={"User-Agent": "nv-br"}, timeout=30.0
        ) as client:
            for page in range(1, MAX_PAGES + 1):
                api_url = (
                    f"{ZKB_API}/kills/solarSystemID/{system_id}/page/{page}/"
                )
                resp = await client.get(api_url)
                if resp.status_code == 200:
                    data: list[dict[str, object]] = resp.json()
                    if not data:
                        break
                    for entry in data:
                        km_time_str = entry.get("killmail_time", "")
                        if km_time_str:
                            km_time = dt.datetime.fromisoformat(
                                str(km_time_str).replace("Z", "+00:00")
                            )
                            if window_start <= km_time <= window_end:
                                km_id = entry["killmail_id"]
                                zkb = entry.get("zkb", {})
                                assert isinstance(zkb, dict)
                                km_hash = zkb.get("hash", "")
                                if km_hash:
                                    refs.append((int(str(km_id)), str(km_hash)))
                elif resp.status_code == 404:
                    break
                else:
                    log.warning(
                        "zkb.api_error",
                        status=resp.status_code,
                        page=page,
                        system_id=system_id,
                    )
                    break

        return ResolvedBr(
            source="zkb",
            source_ref=f"{system_id}/{dt_str}",
            title=None,
            refs=refs,
        )
