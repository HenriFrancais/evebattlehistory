"""SQLAlchemy 2.0 ORM models for the NV Battle Reports database."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

_FK: dict[str, object] = dict(deferrable=True, initially="DEFERRED")


class Base(DeclarativeBase):
    pass


class SolarSystem(Base):
    __tablename__ = "solar_system"

    system_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    security: Mapped[float | None] = mapped_column(Float, nullable=True)


class InventoryType(Base):
    __tablename__ = "inventory_type"

    type_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    group_id: Mapped[int] = mapped_column(Integer, default=0)
    group_name: Mapped[str] = mapped_column(String(64), default="Unknown")
    category_id: Mapped[int] = mapped_column(Integer, default=0)
    category_name: Mapped[str] = mapped_column(String(64), default="Unknown")
    market_group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SdeMeta(Base):
    __tablename__ = "sde_meta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    build_number: Mapped[int] = mapped_column(Integer, default=0)


class Alliance(Base):
    __tablename__ = "alliance"

    alliance_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime)


class Corporation(Base):
    __tablename__ = "corporation"

    corporation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    alliance_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("alliance.alliance_id", **_FK), nullable=True  # type: ignore[arg-type]
    )
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime)


class Character(Base):
    __tablename__ = "character"

    character_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    corporation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("corporation.corporation_id", **_FK), nullable=True  # type: ignore[arg-type]
    )
    alliance_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("alliance.alliance_id", **_FK), nullable=True  # type: ignore[arg-type]
    )
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime)


class Killmail(Base):
    __tablename__ = "killmail"

    killmail_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    killmail_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    solar_system_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("solar_system.system_id", **_FK)  # type: ignore[arg-type]
    )
    victim_character_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("character.character_id", **_FK), nullable=True  # type: ignore[arg-type]
    )
    victim_corporation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("corporation.corporation_id", **_FK), nullable=True  # type: ignore[arg-type]
    )
    victim_alliance_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("alliance.alliance_id", **_FK), nullable=True  # type: ignore[arg-type]
    )
    victim_ship_type_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("inventory_type.type_id", **_FK)  # type: ignore[arg-type]
    )
    total_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    fitted_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    npc_kill: Mapped[bool] = mapped_column(Boolean, default=False)
    solo_kill: Mapped[bool] = mapped_column(Boolean, default=False)
    points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # prod: ALTER TABLE killmail ADD COLUMN damage_taken INTEGER;
    # NOTE: damage_taken backfills only on killmail re-ingest (ESI/zKB pipeline re-run).
    # Running `python -m app.logs.reparse` does NOT backfill this column — reparse only
    # replays gamelogs and updates LogEvent columns (source_name, target_name, etc.).
    damage_taken: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_killmail_solar_system_id", "solar_system_id"),
        Index("ix_killmail_killmail_time", "killmail_time"),
    )


class KillmailAttacker(Base):
    __tablename__ = "killmail_attacker"

    killmail_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("killmail.killmail_id", ondelete="CASCADE", **_FK),  # type: ignore[arg-type]
        primary_key=True,
    )
    attacker_idx: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    character_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("character.character_id", **_FK), nullable=True  # type: ignore[arg-type]
    )
    corporation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("corporation.corporation_id", **_FK), nullable=True  # type: ignore[arg-type]
    )
    alliance_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("alliance.alliance_id", **_FK), nullable=True  # type: ignore[arg-type]
    )
    ship_type_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("inventory_type.type_id", **_FK), nullable=True  # type: ignore[arg-type]
    )
    weapon_type_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("inventory_type.type_id", **_FK), nullable=True  # type: ignore[arg-type]
    )
    damage_done: Mapped[int] = mapped_column(Integer, default=0)
    final_blow: Mapped[bool] = mapped_column(Boolean, default=False)
    security_status: Mapped[float | None] = mapped_column(Float, nullable=True)


class KillmailItem(Base):
    __tablename__ = "killmail_item"

    killmail_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("killmail.killmail_id", ondelete="CASCADE", **_FK),  # type: ignore[arg-type]
        primary_key=True,
    )
    item_idx: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    type_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("inventory_type.type_id", **_FK)  # type: ignore[arg-type]
    )
    flag: Mapped[int] = mapped_column(SmallInteger)
    location: Mapped[str] = mapped_column(String(16))
    qty_destroyed: Mapped[int] = mapped_column(Integer, default=0)
    qty_dropped: Mapped[int] = mapped_column(Integer, default=0)
    singleton: Mapped[bool] = mapped_column(Boolean, default=False)


class BattleReport(Base):
    __tablename__ = "battle_report"

    br_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(16))
    source_url: Mapped[str] = mapped_column(Text)
    source_ref: Mapped[str] = mapped_column(String(128))
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user: Mapped[str] = mapped_column(String(128))
    created_by_char_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    progress_pct: Mapped[int] = mapped_column(Integer, default=0)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    km_count: Mapped[int] = mapped_column(Integer, default=0)

    # BR-level fight rollup (populated by aggregate_br in Task 1.2)
    our_isk_destroyed: Mapped[float] = mapped_column(Float, default=0.0)
    our_isk_lost: Mapped[float] = mapped_column(Float, default=0.0)
    isk_efficiency: Mapped[float | None] = mapped_column(Float, nullable=True)
    result: Mapped[str | None] = mapped_column(String(8), nullable=True)
    battle_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    fight_count: Mapped[int] = mapped_column(Integer, default=0)


class BrSource(Base):
    """One source entry for a BR — a zKB/Aurora link or a system+time-window."""

    __tablename__ = "br_source"

    source_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    br_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("battle_report.br_id", **_FK)  # type: ignore[arg-type]
    )
    # "link" | "window"
    kind: Mapped[str] = mapped_column(String(16))
    # For kind=link
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # For kind=window
    system_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    window_start: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    window_end: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Optional display label
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "pending" | "ok" | "error"
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    km_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_br_source_br_id", "br_id"),)


class BrKillmail(Base):
    __tablename__ = "br_killmail"

    br_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("battle_report.br_id", **_FK), primary_key=True  # type: ignore[arg-type]
    )
    killmail_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("killmail.killmail_id", **_FK), primary_key=True  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Fight-analysis tables (Task 1.2)
# ---------------------------------------------------------------------------


class Fight(Base):
    __tablename__ = "fight"

    fight_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    system_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("solar_system.system_id", **_FK)  # type: ignore[arg-type]
    )
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    isk_destroyed_total: Mapped[float] = mapped_column(Float, default=0.0)
    largest_side_pilots: Mapped[int] = mapped_column(Integer, default=0)
    capitals_involved: Mapped[bool] = mapped_column(Boolean, default=False)
    distinct_alliance_count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        Index("ix_fight_started_at", "started_at"),
        Index("ix_fight_system_id", "system_id"),
    )


class FightSide(Base):
    __tablename__ = "fight_side"

    fight_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("fight.fight_id", ondelete="CASCADE", **_FK),  # type: ignore[arg-type]
        primary_key=True,
    )
    side_idx: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    pilot_count: Mapped[int] = mapped_column(Integer, default=0)
    isk_lost: Mapped[float] = mapped_column(Float, default=0.0)
    alliance_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    corp_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    side_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)


class FightKill(Base):
    __tablename__ = "fight_kill"

    fight_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("fight.fight_id", ondelete="CASCADE", **_FK),  # type: ignore[arg-type]
        primary_key=True,
    )
    killmail_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("killmail.killmail_id", **_FK),  # type: ignore[arg-type]
        primary_key=True,
    )
    side_idx: Mapped[int] = mapped_column(SmallInteger, default=0)

    __table_args__ = (Index("ix_fight_kill_killmail_id", "killmail_id"),)


class BrFight(Base):
    __tablename__ = "br_fight"

    br_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("battle_report.br_id", **_FK),  # type: ignore[arg-type]
        primary_key=True,
    )
    fight_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("fight.fight_id", ondelete="CASCADE", **_FK),  # type: ignore[arg-type]
        primary_key=True,
    )
    seq: Mapped[int] = mapped_column(Integer, default=0)


class FightShipCount(Base):
    __tablename__ = "fight_ship_count"

    fight_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("fight.fight_id", ondelete="CASCADE", **_FK),  # type: ignore[arg-type]
        primary_key=True,
    )
    side_idx: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    ship_type_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("inventory_type.type_id", **_FK),  # type: ignore[arg-type]
        primary_key=True,
    )
    count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (Index("ix_fight_ship_count_type", "ship_type_id", "count"),)


class BrShipCount(Base):
    __tablename__ = "br_ship_count"

    br_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("battle_report.br_id", **_FK),  # type: ignore[arg-type]
        primary_key=True,
    )
    side_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    ship_type_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("inventory_type.type_id", **_FK),  # type: ignore[arg-type]
        primary_key=True,
    )
    count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (Index("ix_br_ship_count_type", "ship_type_id", "count"),)


class BrSideOverride(Base):
    """FC/HC manual side assignment for an entity within one battle report.

    The three permanent NV blues are always friendly (config baseline); every
    other entity is hostile by default. An override lets FC/HC reclassify a
    specific alliance/corp for a single BR (e.g. a blue that helped in that
    engagement). entity_type is 'alliance' or 'corp'; side is 'friendly' or
    'hostile'.
    """

    __tablename__ = "br_side_override"

    br_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("battle_report.br_id", ondelete="CASCADE", **_FK),  # type: ignore[arg-type]
        primary_key=True,
    )
    entity_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    entity_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    side: Mapped[str] = mapped_column(String(16))


# ---------------------------------------------------------------------------
# Gamelog tables (Task 2.2)
# ---------------------------------------------------------------------------


class GamelogFile(Base):
    __tablename__ = "gamelog_file"

    file_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uploaded_by_user: Mapped[str] = mapped_column(String(128))
    claimed_character_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    listener_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    character_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # "filename" | "listener_roster" | "unresolved"
    resolved_via: Mapped[str] = mapped_column(String(32))
    session_started_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    log_start_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    log_end_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stored_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    mime: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    # "parsed" | "unresolved" | "error"
    parse_status: Mapped[str] = mapped_column(String(16))
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_gamelog_file_uploaded_by_user", "uploaded_by_user"),
        Index("ix_gamelog_file_sha256", "sha256"),
    )


# Schema is created via Base.metadata.create_all (no Alembic).
# An existing populated DB needs:
#   ALTER TABLE log_event ADD COLUMN source_name VARCHAR(128);
#   ALTER TABLE log_event ADD COLUMN target_name VARCHAR(128);
#   ALTER TABLE log_event ADD COLUMN authoritative BOOLEAN DEFAULT 0;
#   ALTER TABLE log_event ADD COLUMN dedupe_suppressed BOOLEAN DEFAULT 0;
# Then reparse: python -m app.logs.reparse
class LogEvent(Base):
    __tablename__ = "log_event"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("gamelog_file.file_id", ondelete="CASCADE", **_FK),  # type: ignore[arg-type]
    )
    character_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    direction: Mapped[str | None] = mapped_column(String(4), nullable=True)
    effect_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality: Mapped[str | None] = mapped_column(String(32), nullable=True)
    other_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    other_corp_ticker: Mapped[str | None] = mapped_column(String(16), nullable=True)
    other_alliance_ticker: Mapped[str | None] = mapped_column(String(16), nullable=True)
    other_ship_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    module_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    authoritative: Mapped[bool] = mapped_column(Boolean, default=False)
    dedupe_suppressed: Mapped[bool] = mapped_column(Boolean, default=False)
    fight_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # filled by Task 2.3

    __table_args__ = (
        Index("ix_log_event_character_id_ts", "character_id", "ts"),
        Index("ix_log_event_file_id", "file_id"),
        Index("ix_log_event_fight_id", "fight_id"),
        Index("ix_log_event_ewar_dedupe", "fight_id", "effect_type", "source_name", "target_name"),
    )


# ---------------------------------------------------------------------------
# Log↔fight association tables (Task 2.3)
# ---------------------------------------------------------------------------

#: Bin width used to floor event timestamps into buckets.
BUCKET_SECONDS: int = 5


class LogEventBucket(Base):
    """Read-optimised 5-second bin aggregate for (fight_id, character_id) log data.

    Rebuilt from scratch every time a file is re-associated so sums stay
    correct regardless of upload order.

    NULL → "" convention
    --------------------
    ``effect_type`` and ``direction`` are part of the composite primary key.
    SQLite does not allow NULL in a primary-key column, so source LogEvent values
    of None are coerced to "" (empty string) when buckets are built in
    ``_rebuild_buckets_for_pairs`` (app/logs/associate.py).

    Phase 3/4 readers MUST treat "" as "unknown/none" for both columns; never
    compare against None or interpret "" as a valid named effect type or direction.
    """

    __tablename__ = "log_event_bucket"

    fight_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    character_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bucket_ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    # "" means the source LogEvent.effect_type was None (see NULL→"" convention above)
    effect_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    # "" means the source LogEvent.direction was None (see NULL→"" convention above)
    direction: Mapped[str] = mapped_column(String(4), primary_key=True)

    sum_amount: Mapped[float] = mapped_column(Float, default=0.0)
    event_count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        Index("ix_log_event_bucket_fight_char", "fight_id", "character_id"),
        Index("ix_log_event_bucket_fight_effect", "fight_id", "effect_type"),
    )
