# Character battle-log download

**Date:** 2026-06-27
**Status:** Approved design — ready for implementation plan

## Goal

Let a user download a single character's EVE gamelog for a given battle report (BR),
straight from the composition panel. The download is **sliced** to the battle's time
window and **cleaned** of EVE/HTML markup, so it reads as plain combat text.

## User-facing behavior

- In the friendly-side composition view (`PilotRow`), each pilot that has uploaded logs
  for the BR (`p.has_logs`) gets a small `btn-mini` **log** button next to the existing
  has-logs dot.
- Clicking it downloads a `.txt` named `{character_name|character_id}-{br_id}.txt`
  containing that character's gamelog lines within the battle window, with markup stripped.
- If the character uploaded more than one file covering the battle, the files are
  concatenated, each preceded by a separator header line.

## Decisions (settled during brainstorming)

| Decision | Choice |
| --- | --- |
| Content | Sliced to the battle's time window (not the whole uploaded file) |
| Cleaning | Always strip EVE/HTML markup via existing `strip_eve_markup` |
| Authorization | Reuse `can_view_character` — FC/HC (any character), others own only |
| Multiple files | Concatenate with per-file separator header |
| Window pad | ±60s around `[min(Fight.started_at), max(Fight.ended_at)]` |
| Scope | BR-scoped (UI has `br_id` + `character_id`, not a single `fight_id`) |

## Architecture

### Backend

**New module `app/logs/extract.py`** — pure/testable except the one DB-reading assembler.

Reuses from `app/logs/parse.py`: `_ENVELOPE_RE`, `_parse_ts`, `strip_eve_markup`.

```python
PAD = timedelta(seconds=60)

def clean_and_slice_gamelog(text: str, start: datetime, end: datetime) -> str:
    """Keep lines whose timestamp is within [start, end] (inclusive); strip markup.

    - Lines with a `[ ts ] (tag) rest` envelope: parse ts; if in-window, emit
      `[ ts ] (tag) {strip_eve_markup(rest)}` (canonical envelope + cleaned content).
    - Lines without an envelope (rare continuations): carried under the most recent
      tracked timestamp; emitted cleaned iff that timestamp is in-window.
    - The original 4-5 line file header (no timestamps, before the first event) is dropped.
    Returns possibly-empty text.
    """
```

```python
async def build_battle_log(
    session: AsyncSession, br_id: str, character_id: int
) -> tuple[str, str] | None:
    """Return (combined_cleaned_text, download_filename), or None if the character
    has no logs associated with this BR.

    1. fight_ids  = select(BrFight.fight_id).where(BrFight.br_id == br_id)
    2. window     = select(min(Fight.started_at), max(Fight.ended_at))
                      .where(Fight.fight_id.in_(fight_ids))
                    -> pad by ±PAD. If no fights / null bounds -> None.
    3. file_ids   = select(distinct LogEvent.file_id)
                      .where(LogEvent.fight_id.in_(fight_ids),
                             LogEvent.character_id == character_id)
                    If empty -> None.
    4. For each GamelogFile (ordered by log_start_at, then file_id):
         - read stored_path (errors="replace"); unreadable -> log + skip (per-file
           guard, mirroring app/logs/reparse.py).
         - sliced = clean_and_slice_gamelog(text, start, end)
         - prepend separator:
           `=== file: {original_filename} ({log_start_at}-{log_end_at}) ===`
       Concatenate kept sections (blank line between).
       If nothing kept (all files unreadable/empty) -> None.
    5. filename = f"{sanitize(character_name or character_id)}-{br_id}.txt"
    """
```

`sanitize` reduces the character name to an ASCII filename-safe token (spaces -> `_`,
strip path/quote chars) for the `Content-Disposition` header.

**New endpoint in `app/api/logs.py`:**

```python
@router.get("/api/brs/{br_id}/logs/{character_id}/download")
async def download_character_battle_log(
    br_id: str, character_id: int, request: Request, session: SessionDep
) -> Response:
    user = current_user(request)
    if not await can_view_character(user, character_id):
        raise HTTPException(status_code=403, detail="not allowed to view this character")
    result = await build_battle_log(session, br_id, character_id)
    if result is None:
        raise HTTPException(status_code=404, detail="no logs for this character in this battle")
    text, filename = result
    return Response(
        content=text,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

Follows existing patterns: `current_user(request)` (not `Depends`), `SessionDep`,
router registered under `settings.url_prefix` in `app/main.py`.

### Frontend

**`frontend/src/api.ts`** — new helper (download needs `_impersonateHeaders()` so dev
impersonation authorizes; a plain `<a href>` would not carry that header):

```ts
async downloadCharacterLog(brId: string, characterId: number): Promise<{ blob: Blob; filename: string }> {
  const res = await fetch(`${API}/brs/${brId}/logs/${characterId}/download`, {
    headers: { ..._impersonateHeaders() },
  })
  if (!res.ok) { /* parse {detail}; throw ApiError(res.status, detail) */ }
  const filename = parseContentDisposition(res.headers.get('Content-Disposition'))
                   ?? `${characterId}-${brId}.txt`
  return { blob: await res.blob(), filename }
}
```

**`frontend/src/components/FleetsPanel.tsx` `PilotRow`** — already receives `brId`,
`p.character_id`, `p.character_name`, `p.has_logs`, and `showLogs` (friendly-only):

- When `showLogs && p.has_logs`, render a `btn-mini` **log** button after the name.
- On click: local `busy` state; call `api.downloadCharacterLog(brId, p.character_id)`;
  create an object URL, click a synthetic `<a download={filename}>`, revoke the URL.
- On `ApiError`: surface inline (e.g. `title`/small text); 404 -> "no log in window".
- Disable the button while `busy`.

## Error handling

- 403 — caller may not view this character (`can_view_character` false).
- 404 — no associated files, or nothing survived slicing.
- Unreadable file on disk — skipped with a logged warning, never aborts the whole download.
- Empty window (BR with no fights / null bounds) — treated as no-logs -> 404.

## Testing

Unit (`tests/` — `clean_and_slice_gamelog`):
- header block dropped; in-window kept, out-of-window dropped; boundary inclusivity.
- markup stripped (`<color>`, `<b>`, `<font>`, `&nbsp;`, `<i>` custom ship name).
- continuation line carried under prior timestamp; empty result on no in-window lines.

Unit/integration (`build_battle_log`):
- single file; multi-file concatenation order + separator header.
- no logs -> None; unreadable file skipped; all-unreadable -> None.

API:
- 200 + `Content-Disposition` attachment for an allowed caller.
- 403 for a non-elevated caller requesting another user's character.
- 404 when the character has no logs in the battle.

## Out of scope

- Per-individual-fight downloads (UI is BR-scoped).
- Zip packaging (concatenation chosen instead).
- A `?raw=1` un-cleaned escape hatch (trivial to add later if needed).
- Any change to upload/parse/ingest paths.
