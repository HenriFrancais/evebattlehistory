import io
import json
import zipfile
from pathlib import Path

from app.sde.refresh import refresh_sde


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("types.jsonl", '{"_key":670,"groupID":29,"name":{"en":"Capsule"},"published":true}\n')
        z.writestr("groups.jsonl", '{"_key":29,"categoryID":6,"name":{"en":"Capsule"}}\n')
    return buf.getvalue()


def test_refresh_downloads_when_build_advances(tmp_path: Path):
    n = refresh_sde(tmp_path, fetch_manifest=lambda: '{"buildNumber": 100}', fetch_zip=_zip_bytes)
    assert n == 100
    rows = [json.loads(x) for x in (tmp_path / "inventory_types.jsonl").read_text().splitlines()]
    assert rows[0]["type_id"] == 670 and rows[0]["category_id"] == 6
    assert json.loads((tmp_path / "manifest.json").read_text())["buildNumber"] == 100


def test_refresh_skips_when_unchanged(tmp_path: Path):
    (tmp_path / "manifest.json").write_text('{"buildNumber": 100}')
    (tmp_path / "inventory_types.jsonl").write_text("")
    calls = []
    n = refresh_sde(tmp_path, fetch_manifest=lambda: '{"buildNumber": 100}',
                    fetch_zip=lambda: calls.append(1) or b"")
    assert n is None and calls == []  # no download


def test_force_redownloads(tmp_path: Path):
    (tmp_path / "manifest.json").write_text('{"buildNumber": 100}')
    n = refresh_sde(tmp_path, force=True, fetch_manifest=lambda: '{"buildNumber": 100}', fetch_zip=_zip_bytes)
    assert n == 100
