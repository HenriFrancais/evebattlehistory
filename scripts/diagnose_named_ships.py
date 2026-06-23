"""Diagnostic: surface log events whose parsed party-name looks like a custom
(user-entered) ship name rather than a real pilot.

Runs the REAL parser (parse_log) + SDE splitter (split_entity) over every stored
gamelog file, exactly mirroring ingest, and prints each event whose resulting
other_name / source_name / target_name contains characters a normal EVE pilot
name can never have (brackets, parens, '*', non-ASCII, etc.) — alongside the raw
log line that produced it, so the parser can be fixed against real input.

Read-only: opens the DB only to load the SDE entity-name set and the list of
stored files. Writes nothing. Output is grouped + deduped by raw-line shape.

Run on the VM (same venv as the app):
    python -m scripts.diagnose_named_ships          # text report to stdout
    python -m scripts.diagnose_named_ships > /tmp/named_ships.txt
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db.engine import get_sessionmaker
from app.db.models import GamelogFile
from app.logs.entity import split_entity
from app.logs.parse import parse_log
from app.sde.load import entity_name_set

# A real EVE character name is letters/digits/space/'/-/. only (1–3 words). Anything
# with a bracket, paren, asterisk, non-ASCII glyph, etc. is a custom ship name leak.
_SUSPECT_RE = re.compile(r"[\[\]()<>*£=]|[^\x00-\x7f]")


def _suspect(name: str | None) -> bool:
    return bool(name) and bool(_SUSPECT_RE.search(name or ""))


async def main() -> None:
    settings = get_settings()
    sm = get_sessionmaker(settings)
    async with sm() as session:
        entity_names = await entity_name_set(session)
        files = list((await session.execute(select(GamelogFile))).scalars())

        # ---- Section 1: what is CURRENTLY stored (this is what the snapshot shows) ----
        from app.db.models import LogEvent  # local import keeps the top tidy

        print("=" * 72)
        print("SECTION 1 — suspect names ALREADY STORED in log_event (what is displayed)")
        print("=" * 72)
        stored: Counter[tuple[str, str]] = Counter()
        for col_name, col in (
            ("other_name", LogEvent.other_name),
            ("source_name", LogEvent.source_name),
            ("target_name", LogEvent.target_name),
        ):
            for (val,) in (
                await session.execute(select(col).where(col.is_not(None)).distinct())
            ).all():
                if _suspect(val):
                    stored[(col_name, val)] += 1
        if not stored:
            print("  (none stored — the snapshot bug may already be parser-fixed; a reparse would clear it)\n")
        for (col_name, val), _ in sorted(stored.items()):
            print(f"  [{col_name}] {val!r}")
        print()

    print(f"# Section 2 — re-parsing {len(files)} stored gamelog files with the CURRENT parser\n")

    # raw_line -> (field, parsed_value, count, example_file)
    findings: dict[tuple[str, str, str], int] = Counter()
    examples: dict[tuple[str, str, str], str] = {}
    files_scanned = 0
    files_missing = 0

    for gf in files:
        try:
            text = Path(gf.stored_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            files_missing += 1
            continue
        files_scanned += 1
        parsed = parse_log(text)
        for e in parsed.events:
            if not e.effect_type:
                continue
            # Mirror ingest's split_entity refinement so the values match what is stored.
            other = e.other_name
            if e.effect_type != "damage" and not e.other_ship_name and e.other_name:
                char, _ship = split_entity(e.other_name, entity_names)
                other = char if char is not None else e.other_name
            src = split_entity(e.source_name, entity_names)[0] if e.source_name else None
            tgt = split_entity(e.target_name, entity_names)[0] if e.target_name else None

            for field, value in (("other_name", other), ("source_name", src), ("target_name", tgt)):
                if _suspect(value):
                    raw = (e.raw or "").strip()
                    key = (field, value or "", raw)
                    findings[key] += 1
                    examples.setdefault(key, gf.original_filename or gf.stored_path)

    print("=" * 72)
    print("SECTION 2 — current parser output + the RAW line that produced each (the fix input)")
    print("=" * 72)
    print(f"# files scanned: {files_scanned}  (missing on disk: {files_missing})")
    print(f"# distinct suspect (field, parsed_value, raw_line) tuples: {len(findings)}\n")

    # Sort by frequency desc so the most common offenders lead.
    for (field, value, raw), n in sorted(findings.items(), key=lambda kv: -kv[1]):
        print(f"[{field}] parsed={value!r}  x{n}  ({examples[(field, value, raw)]})")
        print(f"    RAW: {raw}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
