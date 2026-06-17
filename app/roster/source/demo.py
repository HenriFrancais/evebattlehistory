"""Fixture-backed roster source (DATA_SOURCE=demo): reads data_demo/users_api.json."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.roster.models import UsersPayload


class DemoRosterSource:
    name = "demo"

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir

    async def fetch_users(self) -> UsersPayload:
        path = self._data_dir / "users_api.json"
        raw = await asyncio.to_thread(path.read_text)
        return UsersPayload.from_api(json.loads(raw))
