"""Per-character access rule and DEV_MODE-only impersonation.

Access rule
-----------
A user may view a character's per-character graphs if:
  - they are elevated (can_create_br → FC / HC), OR
  - the character belongs to them in the roster snapshot.

Impersonation (DEV_MODE only)
------------------------------
When ``settings.dev_mode`` is True, a caller may include the header
``X-Impersonate-User: <roster_user_name>`` to act as that roster user.
The override is IGNORED in production (dev_mode=False).  When the name is
not found in the roster the override is silently discarded and the real
identity is used.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import HTTPException, Request

from app.api.auth import CurrentUser, can_create_br, current_user
from app.config import Settings, get_settings
from app.roster.snapshot import get_roster_store


async def acting_user(
    request: Request,
    settings: Settings | None = None,
) -> CurrentUser:
    """Return the effective user, honouring impersonation in DEV_MODE.

    In production (dev_mode=False) this is always equal to current_user().
    """
    cfg = settings or get_settings()
    real = current_user(request)

    if not cfg.dev_mode:
        return real

    impersonate_name = request.headers.get("x-impersonate-user", "").strip()
    if not impersonate_name:
        return real

    try:
        roster = await get_roster_store(cfg).get()
    except Exception:
        # Roster unavailable; fall back to real identity.
        return real

    roster_user = next(
        (u for u in roster.users if u.user_name == impersonate_name),
        None,
    )
    if roster_user is None:
        return real

    return CurrentUser(
        user_name=roster_user.user_name,
        rank=roster_user.rank,
        teams=list(roster_user.teams),
        main_character_id=str(roster_user.main_character_id or ""),
    )


async def can_view_character(
    acting: CurrentUser,
    character_id: int,
    settings: Settings | None = None,
) -> bool:
    """True if *acting* may view per-character graphs for *character_id*.

    Elevated users (FC / HC via can_create_br) may view any character.
    Others may only view their own characters (roster lookup).
    Safe-default on roster failure: deny non-elevated, allow elevated.
    """
    cfg = settings or get_settings()

    if can_create_br(acting):
        return True

    try:
        roster = await get_roster_store(cfg).get()
    except Exception:
        # Roster unavailable → deny for non-elevated users.
        return False

    # Check via char_to_user mapping.
    owner = roster.char_to_user.get(character_id)
    if owner == acting.user_name:
        return True

    # Fallback: check user_to_chars (redundant but defensive).
    owned_ids = {c.character_id for c in roster.user_to_chars.get(acting.user_name, [])}
    return character_id in owned_ids


@dataclass(frozen=True)
class Viewer:
    """Who is looking, and which pilots' log-derived data they may see.

    Elevated viewers (FC / HC) see everyone. Everyone else sees fleet aggregates
    plus rows about their OWN characters only.
    """

    user: CurrentUser
    elevated: bool
    character_ids: set[int] = field(default_factory=set)
    character_names: set[str] = field(default_factory=set)

    def can_see(self, character_id: int | None) -> bool:
        return self.elevated or (character_id is not None and character_id in self.character_ids)

    def can_see_name(self, name: str | None) -> bool:
        return self.elevated or (name is not None and name.lower() in self.character_names)


async def viewer_scope(request: Request, settings: Settings | None = None) -> Viewer:
    """Resolve the acting user's visibility scope. Roster failure → own main only."""
    cfg = settings or get_settings()
    acting = await acting_user(request, cfg)
    elevated = can_create_br(acting)
    ids: set[int] = set()
    names: set[str] = set()
    if acting.main_character_id and acting.main_character_id.isdigit():
        ids.add(int(acting.main_character_id))
    try:
        roster = await get_roster_store(cfg).get()
    except Exception:
        return Viewer(user=acting, elevated=elevated, character_ids=ids)
    for c in roster.user_to_chars.get(acting.user_name, []):
        ids.add(c.character_id)
        names.add(c.character_name.strip().lower())
    return Viewer(user=acting, elevated=elevated, character_ids=ids, character_names=names)


async def require_elevated(request: Request, settings: Settings | None = None) -> CurrentUser:
    """403 unless the acting user is FC / High Command. Returns the acting user."""
    acting = await acting_user(request, settings or get_settings())
    if not can_create_br(acting):
        raise HTTPException(status_code=403, detail="Forbidden")
    return acting
