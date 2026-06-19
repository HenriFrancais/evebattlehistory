"""Download + process the CCP SDE, cached by build number. Network is injected so
the core is testable; the __main__ entrypoint wires real httpx."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Callable
from pathlib import Path

from app.observability.logging import log
from app.sde.process import process_sde_lines, read_manifest_build

MANIFEST_URL = "https://developers.eveonline.com/static-data/tranquility/latest.jsonl"
ZIP_URL = "https://developers.eveonline.com/static-data/eve-online-static-data-latest-jsonl.zip"
USER_AGENT = "nv-br (NV Tools; contact admin)"


def _cached_build(sde_dir: Path) -> int | None:
    mf = sde_dir / "manifest.json"
    if not mf.exists():
        return None
    return read_manifest_build(mf.read_text())


def refresh_sde(
    sde_dir: Path,
    *,
    force: bool = False,
    fetch_manifest: Callable[[], str],
    fetch_zip: Callable[[], bytes],
) -> int | None:
    """Refresh the processed SDE artifact if the build advanced (or force). Returns the
    new build number, or None when skipped/unchanged."""
    sde_dir.mkdir(parents=True, exist_ok=True)
    latest = read_manifest_build(fetch_manifest())
    if latest is None:
        log.warning("sde.manifest_unreadable")
        return None
    if not force and _cached_build(sde_dir) == latest:
        log.info("sde.up_to_date", build=latest)
        return None

    raw = fetch_zip()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        types_lines = z.read("types.jsonl").decode("utf-8").splitlines()
        groups_lines = z.read("groups.jsonl").decode("utf-8").splitlines()
    rows = process_sde_lines(types_lines, groups_lines)
    with (sde_dir / "inventory_types.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    (sde_dir / "manifest.json").write_text(json.dumps({"buildNumber": latest}))
    log.info("sde.processed", build=latest, types=len(rows))
    return latest


def _http_get_text(url: str) -> str:
    import httpx

    return httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=30.0,
                     follow_redirects=True).raise_for_status().text


def _http_get_bytes(url: str) -> bytes:
    import httpx

    with httpx.stream("GET", url, headers={"User-Agent": USER_AGENT}, timeout=120.0,
                      follow_redirects=True) as r:
        r.raise_for_status()
        return b"".join(r.iter_bytes())


if __name__ == "__main__":  # pragma: no cover
    import sys

    from app.config import get_settings

    force = "--force" in sys.argv
    sde_dir = get_settings().sde_dir
    build = refresh_sde(sde_dir, force=force,
                        fetch_manifest=lambda: _http_get_text(MANIFEST_URL),
                        fetch_zip=lambda: _http_get_bytes(ZIP_URL))
    print(f"SDE build: {build if build is not None else 'unchanged'} → {sde_dir}")
