"""Content-addressed gamelog storage.

Validates raw bytes (size <= max_log_mb, must be a Gamelog file), computes sha256,
and stores under log_dir/<sha256>.txt.  A second call with the same bytes is a
silent no-op (the file already exists).  Nothing is served from this directory.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import NamedTuple

from app.config import Settings
from app.observability.logging import log

_GAMELOG_DIVIDER = b"----"
_GAMELOG_HEADER_MARKER = b"Gamelog"


class StoreResult(NamedTuple):
    sha256: str
    stored_path: Path
    size: int
    mime: str


def validate_and_store(
    raw_bytes: bytes, settings: Settings, sha256: str | None = None
) -> StoreResult:
    """Validate *raw_bytes* as a Gamelog upload and persist to content-addressed storage.

    Raises ``ValueError`` with a human-readable message on:
    - oversize (> ``max_log_mb`` MB)
    - content that does not look like an EVE gamelog (no ``Gamelog`` header block)
    """
    max_bytes = settings.max_log_mb * 1024 * 1024
    if len(raw_bytes) > max_bytes:
        raise ValueError(
            f"File too large: {len(raw_bytes)} bytes exceeds {settings.max_log_mb} MB limit"
        )

    # Must contain the "----...Gamelog" block within the first 512 bytes
    first_512 = raw_bytes[:512]
    if _GAMELOG_DIVIDER not in first_512 or _GAMELOG_HEADER_MARKER not in first_512:
        raise ValueError("not a valid gamelog: missing Gamelog header block")

    sha = sha256 if sha256 is not None else hashlib.sha256(raw_bytes).hexdigest()
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    dest = settings.log_dir / f"{sha}.txt"

    if not dest.exists():
        dest.write_bytes(raw_bytes)
        log.info("logs.store.written", sha256=sha, size=len(raw_bytes))
    else:
        log.debug("logs.store.already_exists", sha256=sha)

    return StoreResult(sha256=sha, stored_path=dest, size=len(raw_bytes), mime="text/plain")
