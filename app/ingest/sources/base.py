from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class BrUnavailable(Exception):
    """Raised when a battle report cannot be retrieved."""


@dataclass
class ResolvedBr:
    source: str  # "aurora" | "zkb" | "demo"
    source_ref: str  # the parsed ref
    title: str | None
    refs: list[tuple[int, str]]  # (km_id, km_hash)


@runtime_checkable
class BrSource(Protocol):
    async def resolve(self, url: str) -> ResolvedBr: ...
