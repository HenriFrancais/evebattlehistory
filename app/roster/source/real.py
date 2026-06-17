"""Real roster source (DATA_SOURCE=real): GET /api/users on the NV Tools portal."""

from __future__ import annotations

import httpx

from app.config import Settings
from app.roster.models import UsersPayload


class RealRosterSource:
    name = "real"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def fetch_users(self) -> UsersPayload:
        base = self._settings.nv_api_url.rstrip("/")
        if not base:
            raise RuntimeError("NV_API_URL not configured")
        token = self._settings.nv_api_token
        headers = {"authorization": f"Bearer {token}"} if token else {}
        async with httpx.AsyncClient(timeout=self._settings.upstream_timeout_s) as client:
            resp = await client.get(f"{base}/users", headers=headers)
            resp.raise_for_status()
            return UsersPayload.from_api(resp.json())
