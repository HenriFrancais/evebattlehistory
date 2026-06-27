"""Assemble a single character's gamelog for one battle report, sliced to the
battle's time window and cleaned of EVE/HTML markup.

``clean_and_slice_gamelog`` is pure (text in, text out). ``build_battle_log`` is the
only DB-touching piece: it resolves the BR's fight window, finds the character's
associated files, reads them off disk, and concatenates the cleaned slices.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BrFight, Fight, GamelogFile, LogEvent
from app.logs.parse import _ENVELOPE_RE, _parse_ts, strip_eve_markup
from app.observability.logging import log

# Combat reps/ewar often bracket the first/last killmail; fight bounds are derived
# from killmail times, so pad the window slightly when selecting raw lines.
PAD = dt.timedelta(seconds=60)


def _to_naive_utc(ts: dt.datetime) -> dt.datetime:
    """Normalise to naive-UTC so comparisons against parsed (naive-UTC) line
    timestamps never mix aware/naive operands."""
    if ts.tzinfo is not None:
        ts = ts.astimezone(dt.UTC).replace(tzinfo=None)
    return ts


def clean_and_slice_gamelog(text: str, start: dt.datetime, end: dt.datetime) -> str:
    """Return the lines of *text* within ``[start, end]`` (inclusive), markup stripped.

    - Envelope lines (``[ ts ] (tag) rest``) are kept iff ``ts`` is in-window and
      re-emitted as ``[ ts ] (tag) {cleaned rest}`` (canonical envelope, clean body).
    - Non-envelope lines (the file header, rare continuations) carry the most recent
      envelope timestamp; they are kept iff that timestamp is in-window. The header
      block precedes any timestamp, so it is dropped.
    """
    start = _to_naive_utc(start)
    end = _to_naive_utc(end)

    out: list[str] = []
    current_in_window = False  # whether the last-seen envelope ts is within [start, end]
    for raw in text.splitlines():
        m = _ENVELOPE_RE.match(raw.strip())
        if m:
            try:
                ts = _parse_ts(m)
            except (ValueError, OverflowError):
                continue
            current_in_window = start <= ts <= end
            if not current_in_window:
                continue
            cleaned = strip_eve_markup(m.group(8) or "")
            y, mo, d = m.group(1), m.group(2), m.group(3)
            h, mi, s = m.group(4), m.group(5), m.group(6)
            out.append(f"[ {y}.{mo}.{d} {h}:{mi}:{s} ] ({m.group(7)}) {cleaned}".rstrip())
        elif current_in_window:
            cleaned = strip_eve_markup(raw)
            if cleaned:
                out.append(cleaned)
    return "\n".join(out)


def _sanitize_filename(token: str) -> str:
    """Reduce *token* to an ASCII filename-safe slug for Content-Disposition."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", token).strip("_")
    return slug or "log"


async def _battle_window(
    session: AsyncSession, fight_ids: list[int]
) -> tuple[dt.datetime, dt.datetime] | None:
    if not fight_ids:
        return None
    row = (
        await session.execute(
            select(func.min(Fight.started_at), func.max(Fight.ended_at)).where(
                Fight.fight_id.in_(fight_ids)
            )
        )
    ).one()
    start, end = row
    if start is None or end is None:
        return None
    return start - PAD, end + PAD


async def build_battle_log(
    session: AsyncSession, br_id: str, character_id: int
) -> tuple[str, str] | None:
    """Return ``(combined_text, download_filename)`` for *character_id*'s gamelog in
    battle *br_id*, sliced to the battle window and markup-cleaned. ``None`` when the
    character has no logs associated with this battle (or nothing survives slicing)."""
    fight_ids = list(
        (await session.execute(select(BrFight.fight_id).where(BrFight.br_id == br_id))).scalars()
    )
    window = await _battle_window(session, fight_ids)
    if window is None:
        return None
    start, end = window

    file_ids = list(
        (
            await session.execute(
                select(LogEvent.file_id)
                .where(LogEvent.fight_id.in_(fight_ids))
                .where(LogEvent.character_id == character_id)
                .distinct()
            )
        ).scalars()
    )
    if not file_ids:
        return None

    files = list(
        (
            await session.execute(
                select(GamelogFile)
                .where(GamelogFile.file_id.in_(file_ids))
                .order_by(GamelogFile.log_start_at, GamelogFile.file_id)
            )
        ).scalars()
    )

    character_name = next((f.character_name for f in files if f.character_name), None)
    sections: list[str] = []
    for gf in files:
        try:
            raw = Path(gf.stored_path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log.warning("extract.file_unreadable", file_id=gf.file_id, error=str(exc))
            continue
        sliced = clean_and_slice_gamelog(raw, start, end)
        if not sliced:
            continue
        span = f"{_iso(gf.log_start_at)}-{_iso(gf.log_end_at)}"
        sections.append(f"=== file: {gf.original_filename or gf.file_id} ({span}) ===\n{sliced}")

    if not sections:
        return None

    token = character_name or str(character_id)
    filename = f"{_sanitize_filename(token)}-{_sanitize_filename(br_id)}.txt"
    return "\n\n".join(sections) + "\n", filename


def _iso(ts: dt.datetime | None) -> str:
    return ts.isoformat() if ts is not None else "?"
