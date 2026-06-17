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
