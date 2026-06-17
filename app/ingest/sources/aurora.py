"""Aurora (br.evetools.org) battle report resolver.

API contract (confirmed from bundle analysis, 2026-06-17):
  GET https://br.evetools.org/br/battle/<id>  → JSON

The exact 200 response field names are unconfirmed (reports expire before capture).
This resolver tries known-likely shapes in priority order:
  1. kills list at key "kills" → list of {killID, hash} or {killmail_id, hash}
  2. kills list at key "killmails"
  3. kills nested under "data" → same shapes
If none match, raises BrUnavailable.

On 404 / report unavailable → raises BrUnavailable with a clear message.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from app.ingest.sources.base import BrUnavailable, ResolvedBr

AURORA_API = "https://br.evetools.org/br/battle"
_BR_RE = re.compile(r"^/br/([^/]+)/?$")


def _parse_aurora_url(url: str) -> str:
    """Extract br_id from https://br.evetools.org/br/<id>."""
    parsed = urlparse(url)
    m = _BR_RE.match(parsed.path)
    if not m:
        raise ValueError(f"Not a valid Aurora BR URL: {url!r}")
    return m.group(1)


def _extract_refs(data: dict[str, object]) -> list[tuple[int, str]] | None:
    """Try multiple known shapes to extract (km_id, hash) refs."""
    for key in ("kills", "killmails"):
        kills = data.get(key)
        if isinstance(kills, list) and kills:
            return _parse_kills_list(kills)
    nested = data.get("data")
    if isinstance(nested, dict):
        for key in ("kills", "killmails"):
            kills = nested.get(key)
            if isinstance(kills, list) and kills:
                return _parse_kills_list(kills)
    return None


def _parse_kills_list(kills: list[object]) -> list[tuple[int, str]] | None:
    refs: list[tuple[int, str]] = []
    for entry in kills:
        if not isinstance(entry, dict):
            continue
        km_id = entry.get("killID") or entry.get("killmail_id")
        km_hash = entry.get("hash")
        if km_id and km_hash:
            refs.append((int(km_id), str(km_hash)))
    return refs if refs else None


class AuroraSource:
    """Resolves an Aurora BR URL to a list of killmail refs."""

    async def resolve(self, url: str) -> ResolvedBr:
        br_id = _parse_aurora_url(url)
        api_url = f"{AURORA_API}/{br_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(api_url)

        if resp.status_code == 404:
            raise BrUnavailable(f"Aurora BR not found: {br_id!r}")
        if resp.status_code != 200:
            raise BrUnavailable(
                f"Aurora API returned {resp.status_code} for BR {br_id!r}"
            )

        try:
            data: dict[str, object] = resp.json()
        except Exception as exc:
            raise BrUnavailable(f"Aurora API returned invalid JSON: {exc}") from exc

        refs = _extract_refs(data)
        if refs is None:
            raise BrUnavailable(
                f"Aurora BR {br_id!r}: could not find kills list in response"
            )

        title_raw = data.get("title")
        title = str(title_raw) if title_raw else None

        return ResolvedBr(
            source="aurora",
            source_ref=br_id,
            title=title,
            refs=refs,
        )
