"""Tests for parse_killmail — victim.damage_taken field."""

from app.killmail.parse import parse_killmail


def _raw(**v):
    return {
        "killmail_id": 1,
        "killmail_time": "2026-06-10T20:00:00Z",
        "solar_system_id": 31002222,
        "victim": {"ship_type_id": 645, "damage_taken": 51234, **v},
        "attackers": [],
        "zkb": {},
    }


def test_victim_damage_taken_parsed():
    assert parse_killmail(_raw()).victim.damage_taken == 51234


def test_victim_damage_taken_defaults_zero():
    raw = _raw()
    del raw["victim"]["damage_taken"]
    assert parse_killmail(raw).victim.damage_taken == 0
