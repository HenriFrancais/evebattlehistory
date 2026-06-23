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

# Safety cap on hourly anchors queried per window (covers a 2-day window).
_MAX_WINDOW_ANCHORS = 49


def _as_utc(d: dt.datetime) -> dt.datetime:
    """Treat a naive datetime as UTC; pass aware datetimes through unchanged."""
    return d if d.tzinfo is not None else d.replace(tzinfo=dt.UTC)


def _kill_time(kill_obj: dict[str, object]) -> dt.datetime | None:
    """Extract the UTC killmail time from a /related/ kill object's ``dttm`` field.

    Shape: ``{"dttm": {"$date": {"$numberLong": "<unix-millis>"}}}``. Returns
    None when the field is missing or malformed.
    """
    dttm = kill_obj.get("dttm")
    if not isinstance(dttm, dict):
        return None
    date = dttm.get("$date")
    if not isinstance(date, dict):
        return None
    ms = date.get("$numberLong")
    try:
        return dt.datetime.fromtimestamp(int(ms) / 1000, dt.UTC)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


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


def _extract_kills_detailed(
    data: object,
) -> dict[int, tuple[str, float | None, dt.datetime | None]]:
    """Extract km_id → (hash, totalValue, killmail_time) from a /api/related/ response.

    Merges teamA.kills and teamB.kills, skipping any entry lacking a valid
    zkb.hash. Deduplicated (first occurrence wins). killmail_time is None when
    the kill object carries no parseable ``dttm``.
    """
    out: dict[int, tuple[str, float | None, dt.datetime | None]] = {}
    if not isinstance(data, dict):
        return out
    summary = data.get("summary")
    if not isinstance(summary, dict):
        return out
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
            if km_id in out:
                continue
            tv = zkb.get("totalValue")
            value = float(tv) if isinstance(tv, (int, float)) else None
            out[km_id] = (km_hash, value, _kill_time(kill_obj))
    return out


def _extract_refs_from_related(
    data: object,
) -> tuple[list[tuple[int, str]], dict[int, float | None]]:
    """Extract (killmail_id, hash) pairs and km_id→totalValue from a /api/related/ response.

    Thin wrapper over :func:`_extract_kills_detailed` for callers that do not
    need per-kill timestamps.
    """
    detailed = _extract_kills_detailed(data)
    refs = [(km_id, km_hash) for km_id, (km_hash, _v, _t) in detailed.items()]
    values = {km_id: v for km_id, (_h, v, _t) in detailed.items()}
    return refs, values


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
    """Fetch killmail refs for a system across a time window via zKB /related/.

    zKill's GET /api/related/{system_id}/{Y%m%d%H%M}/ groups kills into roughly
    1-hour "battle" buckets (the response's ``exHours``), so a single request
    anchored at ``window_start`` only captures kills near that one hour and
    silently drops anything later in a multi-hour window.

    To honour the full ``[window_start, window_end]`` range we step one anchor
    per hour across the window, union the results (dedup by km_id, first hit
    wins), and filter each kill by its real ``killmail_time`` so the precise
    window — not just the hourly buckets — is respected. Kills whose time can't
    be parsed are kept (defensive: better an extra kill than a dropped one).
    """
    start = _as_utc(window_start)
    end = _as_utc(window_end)

    refs: dict[int, str] = {}
    values: dict[int, float | None] = {}

    anchor = start.replace(minute=0, second=0, microsecond=0)
    queried = 0
    while anchor <= end and queried < _MAX_WINDOW_ANCHORS:
        queried += 1
        api_url = f"{ZKB_API}/related/{system_id}/{anchor:%Y%m%d%H%M}/"
        resp = await client.get(api_url)
        if resp.status_code != 200:
            log.warning(
                "zkb.api_error",
                status=resp.status_code,
                system_id=system_id,
                anchor=f"{anchor:%Y%m%d%H%M}",
            )
            anchor += dt.timedelta(hours=1)
            continue
        for km_id, (km_hash, value, ktime) in _extract_kills_detailed(resp.json()).items():
            if km_id in refs:
                continue
            if ktime is not None and not (start <= ktime <= end):
                continue
            refs[km_id] = km_hash
            values[km_id] = value
        anchor += dt.timedelta(hours=1)

    return list(refs.items()), values
