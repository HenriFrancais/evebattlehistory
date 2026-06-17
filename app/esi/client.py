"""Async ESI HTTP client with disk-cache and rate-limit handling."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from app.config import Settings
from app.observability.logging import log

ESI_BASE = "https://esi.evetech.net/latest"


class EsiClient:
    def __init__(
        self,
        cache_dir: Path,
        user_agent: str,
        timeout_s: float = 30.0,
        max_concurrency: int = 6,
    ) -> None:
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._user_agent = user_agent
        self._timeout_s = timeout_s
        self._max_concurrency = max_concurrency
        self._name_cache: dict[int, dict[str, str]] = {}
        self._http = httpx.AsyncClient(
            headers={"User-Agent": user_agent}, timeout=timeout_s
        )

    async def _get(self, url: str, **kwargs: object) -> httpx.Response:
        """GET with rate-limit + 429 handling."""
        resp = await self._http.get(url, **kwargs)  # type: ignore[arg-type]
        rl_remaining = resp.headers.get("X-Ratelimit-Remaining")
        if rl_remaining is not None and float(rl_remaining) < 10:
            await asyncio.sleep(0.5)
        if resp.status_code in (429, 420):
            retry_after = float(resp.headers.get("Retry-After", "5"))
            log.warning("esi.rate_limited", retry_after=retry_after, url=url)
            await asyncio.sleep(retry_after)
            resp = await self._http.get(url, **kwargs)  # type: ignore[arg-type]
        return resp

    async def fetch_killmail(self, km_id: int, km_hash: str) -> dict[str, object]:
        """Fetch a killmail, checking disk cache first."""
        cache_path = self._cache_dir / f"{km_id}.json"
        if cache_path.exists():
            return json.loads(cache_path.read_text())  # type: ignore[no-any-return]
        url = f"{ESI_BASE}/killmails/{km_id}/{km_hash}/"
        try:
            resp = await self._get(url)
            resp.raise_for_status()
            data: dict[str, object] = resp.json()
            cache_path.write_text(json.dumps(data))
            return data
        except Exception as exc:
            log.warning("esi.fetch_killmail_failed", km_id=km_id, error=str(exc))
            raise

    async def fetch_killmails(
        self, refs: list[tuple[int, str]]
    ) -> list[dict[str, object]]:
        """Fetch multiple killmails concurrently with a semaphore."""
        sem = asyncio.Semaphore(self._max_concurrency)
        results: list[dict[str, object]] = []

        async def _fetch(km_id: int, km_hash: str) -> dict[str, object] | None:
            async with sem:
                try:
                    return await self.fetch_killmail(km_id, km_hash)
                except Exception as exc:
                    log.warning(
                        "esi.fetch_killmails_skip", km_id=km_id, error=str(exc)
                    )
                    return None

        tasks = [_fetch(km_id, km_hash) for km_id, km_hash in refs]
        for coro in asyncio.as_completed(tasks):
            result = await coro
            if result is not None:
                results.append(result)
        return results

    async def _post_names_chunk(
        self, ids: list[int]
    ) -> dict[int, dict[str, str]]:
        """POST /universe/names/ for a chunk of ids. Returns {id: {name, category}}."""
        url = f"{ESI_BASE}/universe/names/"
        resp = await self._http.post(url, json=ids, timeout=self._timeout_s)
        resp.raise_for_status()
        data: list[dict[str, object]] = resp.json()
        return {
            int(str(item["id"])): {
                "name": str(item["name"]),
                "category": str(item["category"]),
            }
            for item in data
        }

    async def resolve_names(self, ids: list[int]) -> dict[int, dict[str, str]]:
        """Resolve entity names via ESI /universe/names/, with in-memory cache."""
        uncached = [i for i in ids if i not in self._name_cache]
        for chunk_start in range(0, len(uncached), 1000):
            chunk = uncached[chunk_start : chunk_start + 1000]
            await self._resolve_chunk(chunk)
        return {i: self._name_cache[i] for i in ids if i in self._name_cache}

    async def _resolve_chunk(self, chunk: list[int]) -> None:
        """Resolve a chunk of ids, binary-splitting on 404."""
        if not chunk:
            return
        try:
            result = await self._post_names_chunk(chunk)
            self._name_cache.update(result)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404 and len(chunk) > 1:
                mid = len(chunk) // 2
                await self._resolve_chunk(chunk[:mid])
                await self._resolve_chunk(chunk[mid:])
            else:
                log.warning("esi.names_chunk_failed", ids=chunk, error=str(exc))
        except Exception as exc:
            log.warning("esi.names_failed", error=str(exc))


_esi_client: EsiClient | None = None


def get_esi_client(settings: Settings) -> EsiClient:
    global _esi_client
    if _esi_client is None:
        _esi_client = EsiClient(
            cache_dir=settings.esi_cache_dir,
            user_agent=settings.esi_user_agent,
            timeout_s=settings.upstream_timeout_s,
        )
    return _esi_client


def reset_esi_client_for_tests() -> None:
    global _esi_client
    _esi_client = None
