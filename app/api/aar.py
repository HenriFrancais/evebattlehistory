"""FastAPI router for the AAR (After Action Report) panel.

One shared markdown AAR per BR, plus flat comments and a fixed emoji-reaction
palette.  Authoring/editing/deleting the AAR is gated on ``can_create_br`` (FC /
High Command) — the same elevated check used for BR create/edit.  Commenting and
reacting are open to all authenticated users; a comment is editable only by its
author and deletable by its author or FC / High Command (moderation).

Every write re-enforces its gate server-side; the ``can_manage`` / ``editable`` /
``deletable`` flags returned to the client are UI hints only.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import delete, select

from app.api.access import acting_user
from app.api.auth import CurrentUser, can_create_br
from app.api.deps import SessionDep
from app.api.fleet import _require_br
from app.api.schemas import (
    AAR_REACTIONS,
    AarBodyIn,
    AarBodyOut,
    AarCommentOut,
    AarPanelOut,
    AarViewerOut,
    CommentIn,
    ReactionGroupOut,
    ReactionIn,
    ReactionToggleOut,
)
from app.config import get_settings
from app.db.models import Aar, AarComment, AarReaction
from app.observability.logging import log

router = APIRouter()


async def _require_elevated(request: Request) -> None:
    acting = await acting_user(request, get_settings())
    if not can_create_br(acting):
        raise HTTPException(status_code=403, detail="Forbidden")


def _char_id(acting: CurrentUser) -> int | None:
    cid = acting.main_character_id
    return int(cid) if cid and cid.isdigit() else None


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def _load_aar(session: SessionDep, br_id: str) -> Aar | None:
    return (
        await session.execute(select(Aar).where(Aar.br_id == br_id))
    ).scalars().first()


def _group_reactions(
    rows: list[AarReaction],
    target_type: str,
    target_id: int,
    viewer_name: str,
) -> list[ReactionGroupOut]:
    """Aggregate the reaction rows for one target into per-emoji groups,
    ordered by the fixed palette."""
    by_emoji: dict[str, list[str]] = {}
    for r in rows:
        if r.target_type == target_type and r.target_id == target_id:
            by_emoji.setdefault(r.emoji, []).append(r.user_name)
    groups: list[ReactionGroupOut] = []
    for emoji in AAR_REACTIONS:
        users = by_emoji.get(emoji)
        if not users:
            continue
        groups.append(
            ReactionGroupOut(
                emoji=emoji,
                count=len(users),
                reacted_by_me=viewer_name in users,
                user_names=sorted(users),
            )
        )
    return groups


def _body_out(a: Aar) -> AarBodyOut:
    return AarBodyOut(
        aar_id=a.aar_id,
        body=a.body,
        created_by_user=a.created_by_user,
        created_by_char_id=a.created_by_char_id,
        updated_by_user=a.updated_by_user,
        created_at=a.created_at,
        updated_at=a.updated_at,
    )


@router.get("/api/brs/{br_id}/aar")
async def get_aar(br_id: str, request: Request, session: SessionDep) -> AarPanelOut:
    """The whole AAR panel for *br_id* in one round trip: the report body (or
    null), its comments (chronological, each with its own reactions), the body's
    reactions, and per-viewer flags."""
    await _require_br(br_id, session)
    acting = await acting_user(request, get_settings())
    elevated = can_create_br(acting)
    viewer = acting.user_name

    aar = await _load_aar(session, br_id)
    if aar is None:
        return AarPanelOut(
            aar=None,
            comments=[],
            reactions=[],
            viewer=AarViewerOut(
                user_name=viewer,
                can_manage=elevated,
                allowed_reactions=list(AAR_REACTIONS),
            ),
        )

    reactions = list(
        (
            await session.execute(
                select(AarReaction).where(AarReaction.aar_id == aar.aar_id)
            )
        ).scalars()
    )
    comments = list(
        (
            await session.execute(
                select(AarComment)
                .where(AarComment.aar_id == aar.aar_id)
                .order_by(AarComment.created_at, AarComment.comment_id)
            )
        ).scalars()
    )

    comment_out = [
        AarCommentOut(
            comment_id=c.comment_id,
            author_user=c.author_user,
            author_char_id=c.author_char_id,
            body=c.body,
            created_at=c.created_at,
            updated_at=c.updated_at,
            editable=(c.author_user == viewer),
            deletable=(c.author_user == viewer or elevated),
            reactions=_group_reactions(reactions, "comment", c.comment_id, viewer),
        )
        for c in comments
    ]

    return AarPanelOut(
        aar=_body_out(aar),
        comments=comment_out,
        reactions=_group_reactions(reactions, "aar", aar.aar_id, viewer),
        viewer=AarViewerOut(
            user_name=viewer,
            can_manage=elevated,
            allowed_reactions=list(AAR_REACTIONS),
        ),
    )


@router.put("/api/brs/{br_id}/aar")
async def put_aar(
    br_id: str, payload: AarBodyIn, request: Request, session: SessionDep
) -> AarBodyOut:
    """Create or replace the AAR body for *br_id*.  FC / High Command only."""
    await _require_br(br_id, session)
    await _require_elevated(request)
    acting = await acting_user(request, get_settings())
    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=400, detail="AAR body must not be empty")

    now = _now()
    aar = await _load_aar(session, br_id)
    if aar is None:
        aar = Aar(
            br_id=br_id,
            body=body,
            created_by_user=acting.user_name,
            created_by_char_id=_char_id(acting),
            updated_by_user=acting.user_name,
            created_at=now,
            updated_at=now,
        )
        session.add(aar)
    else:
        aar.body = body
        aar.updated_by_user = acting.user_name
        aar.updated_at = now
    await session.commit()
    await session.refresh(aar)
    log.info("aar.saved", br_id=br_id, user=acting.user_name)
    return _body_out(aar)


@router.delete("/api/brs/{br_id}/aar")
async def delete_aar(
    br_id: str, request: Request, session: SessionDep
) -> dict[str, bool]:
    """Delete the AAR and (cascade) its comments + reactions.  FC / HC only."""
    await _require_br(br_id, session)
    await _require_elevated(request)
    aar = await _load_aar(session, br_id)
    if aar is None:
        raise HTTPException(status_code=404, detail="No AAR for this battle report")
    aar_id = aar.aar_id
    # Explicit teardown (independent of DB cascade support): reactions → comments → aar.
    await session.execute(delete(AarReaction).where(AarReaction.aar_id == aar_id))
    await session.execute(delete(AarComment).where(AarComment.aar_id == aar_id))
    await session.execute(delete(Aar).where(Aar.aar_id == aar_id))
    await session.commit()
    log.info("aar.deleted", br_id=br_id)
    return {"ok": True}


@router.post("/api/brs/{br_id}/aar/comments")
async def create_comment(
    br_id: str, payload: CommentIn, request: Request, session: SessionDep
) -> AarCommentOut:
    """Post a comment on the BR's AAR.  Any authenticated user."""
    await _require_br(br_id, session)
    acting = await acting_user(request, get_settings())
    aar = await _load_aar(session, br_id)
    if aar is None:
        raise HTTPException(status_code=400, detail="No AAR to comment on yet")
    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=400, detail="Comment must not be empty")

    comment = AarComment(
        br_id=br_id,
        aar_id=aar.aar_id,
        author_user=acting.user_name,
        author_char_id=_char_id(acting),
        body=body,
        created_at=_now(),
        updated_at=None,
    )
    session.add(comment)
    await session.commit()
    await session.refresh(comment)
    log.info("aar.comment.created", br_id=br_id, user=acting.user_name)
    return AarCommentOut(
        comment_id=comment.comment_id,
        author_user=comment.author_user,
        author_char_id=comment.author_char_id,
        body=comment.body,
        created_at=comment.created_at,
        updated_at=comment.updated_at,
        editable=True,
        deletable=True,
        reactions=[],
    )


async def _get_comment(session: SessionDep, br_id: str, cid: int) -> AarComment:
    comment = (
        await session.execute(
            select(AarComment)
            .where(AarComment.comment_id == cid)
            .where(AarComment.br_id == br_id)
        )
    ).scalars().first()
    if comment is None:
        raise HTTPException(status_code=404, detail="Comment not found")
    return comment


@router.patch("/api/brs/{br_id}/aar/comments/{cid}")
async def edit_comment(
    br_id: str, cid: int, payload: CommentIn, request: Request, session: SessionDep
) -> AarCommentOut:
    """Edit a comment.  Author only."""
    await _require_br(br_id, session)
    acting = await acting_user(request, get_settings())
    comment = await _get_comment(session, br_id, cid)
    if comment.author_user != acting.user_name:
        raise HTTPException(status_code=403, detail="Only the author may edit a comment")
    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=400, detail="Comment must not be empty")
    comment.body = body
    comment.updated_at = _now()
    await session.commit()
    await session.refresh(comment)

    reactions = list(
        (
            await session.execute(
                select(AarReaction)
                .where(AarReaction.target_type == "comment")
                .where(AarReaction.target_id == cid)
            )
        ).scalars()
    )
    return AarCommentOut(
        comment_id=comment.comment_id,
        author_user=comment.author_user,
        author_char_id=comment.author_char_id,
        body=comment.body,
        created_at=comment.created_at,
        updated_at=comment.updated_at,
        editable=True,
        deletable=True,
        reactions=_group_reactions(reactions, "comment", cid, acting.user_name),
    )


@router.delete("/api/brs/{br_id}/aar/comments/{cid}")
async def delete_comment(
    br_id: str, cid: int, request: Request, session: SessionDep
) -> dict[str, bool]:
    """Delete a comment.  Author or FC / High Command (moderation)."""
    await _require_br(br_id, session)
    acting = await acting_user(request, get_settings())
    comment = await _get_comment(session, br_id, cid)
    if comment.author_user != acting.user_name and not can_create_br(acting):
        raise HTTPException(status_code=403, detail="Forbidden")
    # A comment's reactions hang off aar_id (not the comment), so clear them here.
    await session.execute(
        delete(AarReaction)
        .where(AarReaction.target_type == "comment")
        .where(AarReaction.target_id == cid)
    )
    await session.execute(delete(AarComment).where(AarComment.comment_id == cid))
    await session.commit()
    log.info("aar.comment.deleted", br_id=br_id, cid=cid, user=acting.user_name)
    return {"ok": True}


@router.post("/api/brs/{br_id}/aar/reactions")
async def toggle_reaction(
    br_id: str, payload: ReactionIn, request: Request, session: SessionDep
) -> ReactionToggleOut:
    """Toggle the caller's reaction on the AAR body or a comment.  Any user.

    Adds the reaction if absent, removes it if present.  Returns the updated
    reaction groups for the affected target only.
    """
    await _require_br(br_id, session)
    acting = await acting_user(request, get_settings())
    if payload.emoji not in AAR_REACTIONS:
        raise HTTPException(status_code=400, detail="Unknown reaction")
    if payload.target_type not in ("aar", "comment"):
        raise HTTPException(status_code=400, detail="Unknown target type")

    aar = await _load_aar(session, br_id)
    if aar is None:
        raise HTTPException(status_code=400, detail="No AAR for this battle report")

    # Validate the target belongs to this BR's AAR.
    if payload.target_type == "aar":
        if payload.target_id != aar.aar_id:
            raise HTTPException(status_code=404, detail="Target not found")
    else:
        await _get_comment(session, br_id, payload.target_id)

    existing = (
        await session.execute(
            select(AarReaction)
            .where(AarReaction.target_type == payload.target_type)
            .where(AarReaction.target_id == payload.target_id)
            .where(AarReaction.user_name == acting.user_name)
            .where(AarReaction.emoji == payload.emoji)
        )
    ).scalars().first()

    if existing is not None:
        await session.delete(existing)
    else:
        session.add(
            AarReaction(
                aar_id=aar.aar_id,
                target_type=payload.target_type,
                target_id=payload.target_id,
                user_name=acting.user_name,
                emoji=payload.emoji,
                created_at=_now(),
            )
        )
    await session.commit()

    rows = list(
        (
            await session.execute(
                select(AarReaction)
                .where(AarReaction.target_type == payload.target_type)
                .where(AarReaction.target_id == payload.target_id)
            )
        ).scalars()
    )
    return ReactionToggleOut(
        target_type=payload.target_type,
        target_id=payload.target_id,
        reactions=_group_reactions(
            rows, payload.target_type, payload.target_id, acting.user_name
        ),
    )
