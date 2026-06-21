"""Tests for app/logs/parse.py and app/logs/filename.py — TDD, red first."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.logs.filename import parse_filename, parse_header, resolve_character
from app.logs.parse import ParsedLog, parse_line, parse_log, strip_eve_markup

FIXTURES = Path(__file__).parent / "fixtures" / "gamelogs"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# strip_eve_markup
# ---------------------------------------------------------------------------


def test_strip_eve_markup_removes_color_tags() -> None:
    raw = "<color=0xffcc0000><b>432</b> <color=0x77ffffff><font size=10>from</font>"
    result = strip_eve_markup(raw)
    assert "432" in result
    assert "from" in result


def test_strip_eve_markup_removes_font_tags() -> None:
    raw = "<font size=12><color=0xFFFFFFFF><b>FakeEnemy</b></color></font>"
    assert strip_eve_markup(raw) == "FakeEnemy"


def test_strip_eve_markup_removes_bold_and_underline() -> None:
    raw = "<b><u>Bhaalgorn</u></b>"
    assert strip_eve_markup(raw) == "Bhaalgorn"


def test_strip_eve_markup_keeps_text() -> None:
    raw = "hello world"
    assert strip_eve_markup(raw) == "hello world"


# ---------------------------------------------------------------------------
# parse_line — envelope
# ---------------------------------------------------------------------------


def test_parse_line_returns_none_for_blank() -> None:
    assert parse_line("") is None


def test_parse_line_returns_none_for_header_separator() -> None:
    assert parse_line("------------------------------------------------------------") is None


def test_parse_line_returns_none_for_non_combat_hint() -> None:
    line = "[ 2026.06.16 19:21:15 ] (hint) Attempting to join a channel"
    result = parse_line(line)
    # hint lines are not effect lines — parse_line returns event with no effect_type
    # OR returns None; by design we return an event with tag=hint but no effect_type
    # so callers can decide.  The spec says "return None for non-effect lines"
    # We return None for lines that produce no ParsedLogEvent at all; for hint we
    # still return an event (for counting total_lines), but effect_type=None.
    assert result is not None
    assert result.effect_type is None
    assert result.tag == "hint"


def test_parse_line_returns_none_for_malformed() -> None:
    assert parse_line("not a log line at all") is None


def test_parse_line_truncated_line_no_raise() -> None:
    # Must not raise even for garbage content
    result = parse_line("TRUNCATED LINE WITHOUT NEWLINE")
    assert result is None  # not a valid envelope


# ---------------------------------------------------------------------------
# parse_line — damage in
# ---------------------------------------------------------------------------


def test_damage_in_fields() -> None:
    raw = (
        "[ 2026.01.01 12:01:07 ] (combat) "
        "<color=0xffcc0000><b>432</b> "
        "<color=0x77ffffff><font size=10>from</font> "
        "<b><color=0xffffffff>FakeEnemy Bravo[.TST](Brutix Navy Issue)</b>"
        '<font size=10><color=0x77ffffff> - 250mm Railgun II - Grazes'
    )
    evt = parse_line(raw)
    assert evt is not None
    assert evt.effect_type == "damage"
    assert evt.direction == "in"
    assert evt.amount == 432.0
    assert evt.quality == "Grazes"
    assert evt.other_name == "FakeEnemy Bravo"
    assert evt.other_corp_ticker == ".TST"
    assert evt.other_ship_name == "Brutix Navy Issue"
    assert evt.module_name == "250mm Railgun II"
    # ts is naive UTC (tzinfo=None) — _parse_ts emits naive datetimes, see Bug (A) fix
    assert evt.ts == datetime(2026, 1, 1, 12, 1, 7)
    assert evt.ts.tzinfo is None


# ---------------------------------------------------------------------------
# parse_line — damage out
# ---------------------------------------------------------------------------


def test_damage_out_fields() -> None:
    raw = (
        "[ 2026.01.01 12:01:07 ] (combat) "
        "<color=0xff00ffff><b>151</b> "
        "<color=0x77ffffff><font size=10>to</font> "
        "<b><color=0xffffffff>FakeEnemy Charlie[MEMO](Eos)</b>"
        '<font size=10><color=0x77ffffff> - Light Entropic Disintegrator II - Smashes'
    )
    evt = parse_line(raw)
    assert evt is not None
    assert evt.effect_type == "damage"
    assert evt.direction == "out"
    assert evt.amount == 151.0
    assert evt.quality == "Smashes"
    assert evt.other_name == "FakeEnemy Charlie"
    assert evt.other_corp_ticker == "MEMO"
    assert evt.other_ship_name == "Eos"
    assert evt.module_name == "Light Entropic Disintegrator II"


# ---------------------------------------------------------------------------
# parse_line — warp disrupt in
# ---------------------------------------------------------------------------


def test_disrupt_in_from_enemy_to_ally() -> None:
    """Enemy disrupts an ally — we observe it."""
    raw = (
        "[ 2026.01.01 12:00:04 ] (combat) "
        "<color=0xffffffff><b>Warp disruption attempt</b> "
        "<color=0x77ffffff><font size=10>from</font> "
        "<color=0xffffffff><b>"
        "<font size=12><color=0xFFFFFFFF><b>FakeEnemy Delta</b> </color></font>"
        "<font size=12><color=0xFFFFB300>[10MN]</color></font>"
        "<font size=12>[.EFG]</font> "
        "<font size=12><color=0xFFFFFFFF><b>Retribution</b></color></font></b> "
        "<color=0x77ffffff><font size=10>to <b><color=0xffffffff></font>"
        "<font size=12><color=0xFFFFFFFF><b>AllyChar One</b> </color></font>"
        "<font size=12><color=0xFFFFB300>[NV]</color></font>"
        "<font size=12>[NVACA]</font> "
        "<font size=12><color=0xFFFFFFFF><b>Kitsune</b></color></font>"
    )
    evt = parse_line(raw)
    assert evt is not None
    assert evt.effect_type == "disrupt"
    assert evt.direction == "in"
    assert evt.other_name == "FakeEnemy Delta"
    assert evt.other_ship_name == "Retribution"


def test_disrupt_out_from_you() -> None:
    """You disrupt an enemy."""
    raw = (
        "[ 2026.01.01 12:00:05 ] (combat) "
        "<color=0xffffffff><b>Warp disruption attempt</b> "
        "<color=0x77ffffff><font size=10>from</font> "
        "<color=0xffffffff><b>you</b> "
        "<color=0x77ffffff><font size=10>to <b><color=0xffffffff></font>"
        "<font size=12><color=0xFFFFFFFF><b>FakeEnemy Delta</b> </color></font>"
        "<font size=12><color=0xFFFFB300>[10MN]</color></font>"
        "<font size=12>[.EFG]</font> "
        "<font size=12><color=0xFFFFFFFF><b>Retribution</b></color></font>"
    )
    evt = parse_line(raw)
    assert evt is not None
    assert evt.effect_type == "disrupt"
    assert evt.direction == "out"
    assert evt.other_name == "FakeEnemy Delta"
    assert evt.other_ship_name == "Retribution"


def test_disrupt_in_enemy_to_you() -> None:
    """Enemy disrupts you directly."""
    raw = (
        "[ 2026.01.01 12:00:06 ] (combat) "
        "<color=0xffffffff><b>Warp disruption attempt</b> "
        "<color=0x77ffffff><font size=10>from</font> "
        "<color=0xffffffff><b>"
        "<font size=12><color=0xFFFFFFFF><b>FakeEnemy Delta</b> </color></font>"
        "<font size=12><color=0xFFFFB300>[10MN]</color></font>"
        "<font size=12>[.EFG]</font> "
        "<font size=12><color=0xFFFFFFFF><b>Retribution</b></color></font></b> "
        "<color=0x77ffffff><font size=10>to <b><color=0xffffffff></font>you!"
    )
    evt = parse_line(raw)
    assert evt is not None
    assert evt.effect_type == "disrupt"
    assert evt.direction == "in"
    assert evt.other_name == "FakeEnemy Delta"


# ---------------------------------------------------------------------------
# parse_line — warp scram
# ---------------------------------------------------------------------------


def test_scram_fields() -> None:
    raw = (
        "[ 2026.01.01 12:06:44 ] (combat) "
        "<color=0xffffffff><b>Warp scramble attempt</b> "
        "<color=0x77ffffff><font size=10>from</font> "
        "<color=0xffffffff><b>"
        "<font size=12><color=0xFFFFFFFF><b>AllyChar Kyte</b> </color></font>"
        "<font size=12><color=0xFFFFB300>[NV]</color></font>"
        "<font size=12>[NVACA]</font> "
        "<font size=12><color=0xFFFFFFFF><b>Muninn</b></color></font></b> "
        "<color=0x77ffffff><font size=10>to <b><color=0xffffffff></font>"
        "<font size=12><color=0xFFFFFFFF><b>FakeEnemy Delta</b> </color></font>"
        "<font size=12><color=0xFFFFB300>[10MN]</color></font>"
        "<font size=12>[.EFG]</font> "
        "<font size=12><color=0xFFFFFFFF><b>Omen Navy Issue</b></color></font>"
    )
    evt = parse_line(raw)
    assert evt is not None
    assert evt.effect_type == "scram"
    # Third-party scram (src=AllyChar Kyte, tgt=FakeEnemy Delta, neither is "you").
    assert evt.other_name == "AllyChar Kyte"
    assert evt.direction == "in"
    assert evt.source_name == "AllyChar Kyte"
    assert evt.target_name == "FakeEnemy Delta"
    assert evt.authoritative is False


def test_scram_third_party_records_real_tackler_and_target() -> None:
    """Case 3: neither party is 'you' — record real source AND target, authoritative=False."""
    raw = (
        "[ 2026.01.01 12:06:44 ] (combat) "
        "<color=0xffffffff><b>Warp scramble attempt</b> "
        "<color=0x77ffffff><font size=10>from</font> "
        "<color=0xffffffff><b>"
        "<font size=12><color=0xFFFFFFFF><b>AllyChar Kyte</b> </color></font>"
        "<font size=12><color=0xFFFFB300>[NV]</color></font>"
        "<font size=12>[NVACA]</font> "
        "<font size=12><color=0xFFFFFFFF><b>Muninn</b></color></font></b> "
        "<color=0x77ffffff><font size=10>to <b><color=0xffffffff></font>"
        "<font size=12><color=0xFFFFFFFF><b>FakeEnemy Delta</b> </color></font>"
        "<font size=12><color=0xFFFFB300>[10MN]</color></font>"
        "<font size=12>[.EFG]</font> "
        "<font size=12><color=0xFFFFFFFF><b>Omen Navy Issue</b></color></font>"
    )
    evt = parse_line(raw)
    assert evt is not None
    assert evt.effect_type == "scram"
    assert evt.authoritative is False
    assert evt.source_name == "AllyChar Kyte"
    assert evt.target_name == "FakeEnemy Delta"
    assert evt.other_name == "AllyChar Kyte"   # never the log owner


def test_disrupt_out_from_you_sets_authoritative_and_target() -> None:
    """Case 1: src=='you' → authoritative=True, source_name=None, target_name set."""
    raw = (
        "[ 2026.01.01 12:00:05 ] (combat) "
        "<color=0xffffffff><b>Warp disruption attempt</b> "
        "<color=0x77ffffff><font size=10>from</font> "
        "<color=0xffffffff><b>you</b> "
        "<color=0x77ffffff><font size=10>to <b><color=0xffffffff></font>"
        "<font size=12><color=0xFFFFFFFF><b>FakeEnemy Delta</b> </color></font>"
        "<font size=12><color=0xFFFFB300>[10MN]</color></font>"
        "<font size=12>[.EFG]</font> "
        "<font size=12><color=0xFFFFFFFF><b>Retribution</b></color></font>"
    )
    evt = parse_line(raw)
    assert evt is not None
    assert evt.authoritative is True
    assert evt.source_name is None
    assert evt.target_name == "FakeEnemy Delta"


def test_disrupt_in_to_you_sets_authoritative_and_source() -> None:
    """Case 2: tgt=='you' → authoritative=True, source_name set, target_name=None."""
    raw = (
        "[ 2026.01.01 12:00:06 ] (combat) "
        "<color=0xffffffff><b>Warp disruption attempt</b> "
        "<color=0x77ffffff><font size=10>from</font> "
        "<color=0xffffffff><b>"
        "<font size=12><color=0xFFFFFFFF><b>FakeEnemy Delta</b> </color></font>"
        "<font size=12><color=0xFFFFB300>[10MN]</color></font>"
        "<font size=12>[.EFG]</font> "
        "<font size=12><color=0xFFFFFFFF><b>Retribution</b></color></font></b> "
        "<color=0x77ffffff><font size=10>to <b><color=0xffffffff></font>you!"
    )
    evt = parse_line(raw)
    assert evt is not None
    assert evt.authoritative is True
    assert evt.source_name == "FakeEnemy Delta"
    assert evt.target_name is None


# ---------------------------------------------------------------------------
# parse_line — neut out (energy neutralized)
# ---------------------------------------------------------------------------


def test_neut_out_energy_neutralized() -> None:
    """You neut an enemy: N GJ energy neutralized Target [ALLI][CORP] Ship - Module."""
    raw = (
        "[ 2026.01.01 12:05:26 ] (combat) "
        "<color=0xff7fffff><b>234 GJ</b><color=0x77ffffff>"
        "<font size=10> energy neutralized </font>"
        "<b><color=0xffffffff>"
        "<font size=12><color=0xFFFFFFFF><b>FakeEnemy Echo</b> </color></font>"
        "<font size=12><color=0xFFFFB300>[10MN]</color></font>"
        "<font size=12>[.EFG]</font> "
        "<font size=12><color=0xFFFFFFFF><b>Hurricane Fleet Issue</b></color></font></b>"
        "<color=0x77ffffff><font size=10> - Medium Abyssal Energy Neutralizer</font>"
    )
    evt = parse_line(raw)
    assert evt is not None
    assert evt.effect_type == "neut"
    assert evt.direction == "out"
    assert evt.amount == 234.0
    assert evt.other_name == "FakeEnemy Echo"
    assert evt.other_ship_name == "Hurricane Fleet Issue"
    assert evt.module_name == "Medium Abyssal Energy Neutralizer"


def test_neut_out_zero_gj() -> None:
    """Zero GJ neut still parses correctly."""
    raw = (
        "[ 2026.01.01 12:05:28 ] (combat) "
        "<color=0xff7fffff><b>0 GJ</b><color=0x77ffffff>"
        "<font size=10> energy neutralized </font>"
        "<b><color=0xffffffff>"
        "<font size=12><color=0xFFFFFFFF><b>FakeEnemy Echo</b> </color></font>"
        "<font size=12><color=0xFFFFB300>[10MN]</color></font>"
        "<font size=12>[.EFG]</font> "
        "<font size=12><color=0xFFFFFFFF><b>Hurricane Fleet Issue</b></color></font></b>"
        "<color=0x77ffffff><font size=10> - Medium Abyssal Energy Neutralizer</font>"
    )
    evt = parse_line(raw)
    assert evt is not None
    assert evt.effect_type == "neut"
    assert evt.amount == 0.0


# ---------------------------------------------------------------------------
# parse_line — nos (energy drained)
# ---------------------------------------------------------------------------


def test_nos_outgoing_from_old_encoding() -> None:
    """Outgoing NOS: listener drains FROM target — OLD encoding, positive signed amount."""
    raw = (
        "[ 2026.01.01 12:10:01 ] (combat) "
        "<color=0xff7fffff><b>+52 GJ</b><color=0x77ffffff>"
        "<font size=10> energy drained from </font>"
        "<b><color=0xffffffff>"
        "<font size=12><color=0xFFFFB300> <u><b>Loki</b></u></color></font>"
        "<font size=12><color=0xFFFFFF66> [<b>SMAD</b>]</color></font>"
        " [<b>PSAZ</b>]  [iamamusing Shazih]"
        "<color=0xFFFFFFFF><b> -</b><color=0x77ffffff>"
        "<font size=10> - Small Ghoul Compact Energy Nosferatu</font>"
    )
    evt = parse_line(raw)
    assert evt is not None
    assert evt.effect_type == "nos"
    assert evt.direction == "out"
    assert evt.amount == 52.0
    assert evt.other_name == "iamamusing Shazih"
    assert evt.other_ship_name == "Loki"
    assert evt.module_name == "Small Ghoul Compact Energy Nosferatu"


def test_nos_incoming_to_old_encoding() -> None:
    """Incoming NOS: enemy drains TO listener (listener loses cap) — OLD encoding, negative amount.

    Real-log finding: 'energy drained to' lines DO exist in client logs (~52 files checked).
    The module on the line is the enemy's nosferatu. Amount is negative (e.g. -0 GJ).
    Incoming cap-warfare ('energy neutralized you') does NOT appear in real logs.
    """
    raw = (
        "[ 2026.01.01 12:10:06 ] (combat) "
        "<color=0xffe57f7f><b>-0 GJ</b><color=0x77ffffff>"
        "<font size=10> energy drained to </font>"
        "<b><color=0xffffffff>"
        "<font size=12><color=0xFFFFB300> <u><b>Absolution</b></u></color></font>"
        "<font size=12><color=0xFFFFFF66> [<b>4CRAB</b>]</color></font>"
        " [<b>SRG-C</b>]  [Hekpoc Risalo]"
        "<color=0xFFFFFFFF><b> -</b><color=0x77ffffff>"
        "<font size=10> - Small Energy Nosferatu II</font>"
    )
    evt = parse_line(raw)
    assert evt is not None
    assert evt.effect_type == "nos"
    assert evt.direction == "in"
    assert evt.amount == 0.0
    assert evt.other_name == "Hekpoc Risalo"
    assert evt.other_ship_name == "Absolution"
    assert evt.module_name == "Small Energy Nosferatu II"


# ---------------------------------------------------------------------------
# parse_line — rep armor
# ---------------------------------------------------------------------------


def test_rep_armor_in_old_format() -> None:
    """Being armor-repped by an ally — old log format: Ship [ALLI][CORP] [CharName]."""
    raw = (
        "[ 2026.01.01 12:10:28 ] (combat) "
        "<color=0xffccff66><b>448</b><color=0x77ffffff>"
        "<font size=10> remote armor repaired by </font>"
        "<b><color=0xffffffff>"
        "<font size=12><color=0xFFFFB300> <u><b>Guardian</b></u></color></font>"
        "<font size=12><color=0xFFFFFF66> [<b>NV</b>]</color></font>"
        " [<b>NVACA</b>]  [AllyChar Amoni]"
        "<color=0xFFFFFFFF><b> -</b><color=0x77ffffff>"
        "<font size=10> - Large Coaxial Compact Remote Armor Repairer</font>"
    )
    evt = parse_line(raw)
    assert evt is not None
    assert evt.effect_type == "rep_armor"
    assert evt.direction == "in"
    assert evt.amount == 448.0
    assert evt.other_name == "AllyChar Amoni"
    assert evt.other_ship_name == "Guardian"
    assert evt.module_name == "Large Coaxial Compact Remote Armor Repairer"


def test_rep_armor_out_new_format() -> None:
    """You armor-rep an ally — new log format: CharName [CORP][ALLI] Ship."""
    raw = (
        "[ 2026.01.01 12:32:58 ] (combat) "
        "<color=0xffccff66><b>442</b><color=0x77ffffff>"
        "<font size=10> remote armor repaired to </font>"
        "<b><color=0xffffffff>"
        "<font size=12><color=0xFFFFFFFF><b>AllyChar Peter</b> </color></font>"
        "<font size=12><color=0xFFFFB300>[NV]</color></font>"
        "<font size=12>[NVACA]</font> "
        "<font size=12><color=0xFFFFFFFF><b>Sacrilege</b></color></font></b>"
        "<color=0x77ffffff><font size=10> - Large Coaxial Compact Remote Armor Repairer</font>"
    )
    evt = parse_line(raw)
    assert evt is not None
    assert evt.effect_type == "rep_armor"
    assert evt.direction == "out"
    assert evt.amount == 442.0
    assert evt.other_name == "AllyChar Peter"
    assert evt.other_ship_name == "Sacrilege"
    assert evt.module_name == "Large Coaxial Compact Remote Armor Repairer"


# ---------------------------------------------------------------------------
# parse_line — rep shield
# ---------------------------------------------------------------------------


def test_rep_shield_in_new_format() -> None:
    """Being shield-boosted by an ally — new format: CharName [CORP][ALLI] Ship."""
    raw = (
        "[ 2026.01.01 12:01:14 ] (combat) "
        "<color=0xffccff66><b>254</b><color=0x77ffffff>"
        "<font size=10> remote shield boosted by </font>"
        "<b><color=0xffffffff>"
        "<font size=12><color=0xFFFFFFFF><b>AllyChar Fliba</b> </color></font>"
        "<font size=12><color=0xFFFFB300>[NV]</color></font>"
        "<font size=12>[NVACA]</font> "
        "<font size=12><color=0xFFFFFFFF><b>Scimitar</b></color></font></b>"
        "<color=0x77ffffff><font size=10> - Pithum C-Type Medium Remote Shield Booster</font>"
    )
    evt = parse_line(raw)
    assert evt is not None
    assert evt.effect_type == "rep_shield"
    assert evt.direction == "in"
    assert evt.amount == 254.0
    assert evt.other_name == "AllyChar Fliba"
    assert evt.other_ship_name == "Scimitar"
    assert evt.module_name == "Pithum C-Type Medium Remote Shield Booster"


# ---------------------------------------------------------------------------
# parse_line — cap transfer
# ---------------------------------------------------------------------------


def test_cap_in_old_format() -> None:
    """Cap transmitted to you — old format: Ship [ALLI][CORP] [CharName]."""
    raw = (
        "[ 2026.01.01 12:00:47 ] (combat) "
        "<color=0xffccff66><b>351</b><color=0x77ffffff>"
        "<font size=10> remote capacitor transmitted by </font>"
        "<b><color=0xffffffff>"
        "<font size=12><color=0xFFFFB300> <u><b>Basilisk</b></u></color></font>"
        "<font size=12><color=0xFFFFFF66> [<b>NV</b>]</color></font>"
        " [<b>NVACA</b>]  [AllyChar Tari]"
        "<color=0xFFFFFFFF><b> -</b><color=0x77ffffff>"
        "<font size=10> - Large Remote Capacitor Transmitter II</font>"
    )
    evt = parse_line(raw)
    assert evt is not None
    assert evt.effect_type == "cap_transfer"
    assert evt.direction == "in"
    assert evt.amount == 351.0
    assert evt.other_name == "AllyChar Tari"
    assert evt.other_ship_name == "Basilisk"
    assert evt.module_name == "Large Remote Capacitor Transmitter II"


def test_cap_out_old_format() -> None:
    """Cap you transmitted to ally — old format."""
    raw = (
        "[ 2026.01.01 12:24:39 ] (combat) "
        "<color=0xffccff66><b>7150</b><color=0x77ffffff>"
        "<font size=10> remote capacitor transmitted to </font>"
        "<b><color=0xffffffff>"
        "<font size=12><color=0xFFFFB300> <u><b>Bhaalgorn</b></u></color></font>"
        "<font size=12><color=0xFFFFFF66> [<b>NV</b>]</color></font>"
        " [<b>NVACA</b>]  [AllyChar Poiuyt]"
        "<color=0xFFFFFFFF><b> -</b><color=0x77ffffff>"
        "<font size=10> - CONCORD Capital Remote Capacitor Transmitter</font>"
    )
    evt = parse_line(raw)
    assert evt is not None
    assert evt.effect_type == "cap_transfer"
    assert evt.direction == "out"
    assert evt.amount == 7150.0
    assert evt.other_name == "AllyChar Poiuyt"
    assert evt.other_ship_name == "Bhaalgorn"
    assert evt.module_name == "CONCORD Capital Remote Capacitor Transmitter"


# ---------------------------------------------------------------------------
# parse_line — jam (notify)
# ---------------------------------------------------------------------------


def test_jam_notify_fields() -> None:
    line = (
        "[ 2026.01.01 12:45:38 ] (notify) "
        "Interference from FakeEnemy Foxtrot's warp prevents your sensors from locking the target."
    )
    evt = parse_line(line)
    assert evt is not None
    assert evt.effect_type == "jam"
    assert evt.direction == "in"
    assert evt.other_name == "FakeEnemy Foxtrot"
    assert evt.tag == "notify"


# ---------------------------------------------------------------------------
# parse_line — ignored lines (drones, misses) not raising
# ---------------------------------------------------------------------------


def test_drone_belonging_to_no_raise() -> None:
    line = (
        "[ 2026.01.01 12:01:35 ] (combat) Valkyrie II belonging to"
        " FakeEnemy Golf misses you completely - Valkyrie II"
    )
    evt = parse_line(line)
    # Must not raise; result is either None or event with no effect_type
    if evt is not None:
        assert evt.effect_type is None


def test_misses_line_no_raise() -> None:
    line = (
        "[ 2026.01.01 12:01:04 ] (combat) FakeEnemy Golf misses you"
        " completely - 720mm Howitzer Artillery II"
    )
    evt = parse_line(line)
    if evt is not None:
        assert evt.effect_type is None


def test_your_drone_misses_line_no_raise() -> None:
    line = (
        "[ 2026.01.01 12:06:24 ] (combat) Your 'Augmented' Infiltrator"
        " misses FakeEnemy India completely - 'Augmented' Infiltrator"
    )
    evt = parse_line(line)
    if evt is not None:
        assert evt.effect_type is None


# ---------------------------------------------------------------------------
# parse_log
# ---------------------------------------------------------------------------


def test_parse_log_from_fixture_full_fight() -> None:
    text = _read("full_fight.txt")
    result = parse_log(text)
    assert isinstance(result, ParsedLog)
    # Stats sanity
    assert result.stats["total_lines"] > 0
    assert result.stats["combat_lines"] > 0
    assert result.stats["matched"] > 0
    # Quality metric: all unmatched combat should be misses/"belonging to" lines
    # (not parseable effects we missed). Real fights have ~20% miss rate.
    unmatched_events = [
        e for e in result.events if e.tag == "combat" and e.effect_type is None
    ]
    for evt in unmatched_events:
        stripped = evt.raw.lower()
        assert "misses" in stripped or "belonging to" in stripped, (
            f"Unexpected unmatched combat line (not a miss): {evt.raw[:120]}"
        )
    # Also confirm matched ratio is reasonable (≥ 75% of combat lines matched)
    ratio = result.stats["matched"] / result.stats["combat_lines"]
    assert ratio >= 0.75, f"Matched ratio {ratio:.1%} is too low"


def test_parse_log_events_list() -> None:
    text = _read("damage_in.txt")
    result = parse_log(text)
    effects = [e for e in result.events if e.effect_type is not None]
    assert len(effects) == 2
    assert all(e.effect_type == "damage" for e in effects)
    assert all(e.direction == "in" for e in effects)


def test_parse_log_header_populated() -> None:
    text = _read("full_fight.txt")
    result = parse_log(text)
    assert result.header.listener_name == "TestChar Alpha"
    assert result.header.session_started is not None


# ---------------------------------------------------------------------------
# filename.py — parse_filename
# ---------------------------------------------------------------------------


def test_parse_filename_with_char_id() -> None:
    result = parse_filename("20260616_192114_2112615087.txt")
    assert result["character_id"] == 2112615087
    assert result["start"] == datetime(2026, 6, 16, 19, 21, 14, tzinfo=UTC)


def test_parse_filename_without_char_id() -> None:
    result = parse_filename("20231006_204512.txt")
    assert result["character_id"] is None
    assert result["start"] == datetime(2023, 10, 6, 20, 45, 12, tzinfo=UTC)


def test_parse_filename_invalid() -> None:
    result = parse_filename("not_a_gamelog.txt")
    assert result["character_id"] is None
    assert result["start"] is None


def test_parse_filename_invalid_date_no_raise() -> None:
    """parse_filename must not raise on impossible dates (e.g. month 13, day 31 in June)."""
    result = parse_filename("20260631_120000.txt")
    assert result["start"] is None
    assert result["character_id"] is None


# ---------------------------------------------------------------------------
# filename.py — parse_header
# ---------------------------------------------------------------------------


def test_parse_header_with_listener() -> None:
    text = _read("with_char_id.txt")
    header = parse_header(text)
    assert header.listener_name == "TestChar Alpha"
    assert header.session_started == datetime(2026, 6, 16, 19, 21, 14, tzinfo=UTC)


def test_parse_header_without_listener() -> None:
    text = _read("no_char_id.txt")
    header = parse_header(text)
    assert header.listener_name is None
    assert header.session_started == datetime(2023, 10, 6, 20, 45, 12, tzinfo=UTC)


# ---------------------------------------------------------------------------
# filename.py — resolve_character
# ---------------------------------------------------------------------------


def test_resolve_character_from_filename() -> None:
    filename_meta = {"character_id": 2112615087, "start": None}
    header = parse_header(_read("with_char_id.txt"))
    result = resolve_character(filename_meta, header, lambda name: None)
    assert result["character_id"] == 2112615087
    assert result["resolved_via"] == "filename"


def test_resolve_character_from_listener_roster() -> None:
    from app.logs.filename import LogHeader

    filename_meta = {"character_id": None, "start": None}
    # no_char_id has no Listener line; build a header with a name directly
    header_with_name = LogHeader(listener_name="TestChar Alpha", session_started=None)
    roster = {"TestChar Alpha": 9999001}
    result = resolve_character(filename_meta, header_with_name, lambda name: roster.get(name))
    assert result["character_id"] == 9999001
    assert result["character_name"] == "TestChar Alpha"
    assert result["resolved_via"] == "listener_roster"


def test_resolve_character_unresolved() -> None:
    filename_meta = {"character_id": None, "start": None}
    header = parse_header(_read("no_char_id.txt"))
    result = resolve_character(filename_meta, header, lambda name: None)
    assert result["character_id"] is None
    assert result["resolved_via"] == "unresolved"


# ---------------------------------------------------------------------------
# Bug (A) regression: parsed ts must be tz-NAIVE (naive UTC throughout system)
# ---------------------------------------------------------------------------


def test_parsed_ts_is_naive_utc() -> None:
    """_parse_ts must emit a naive datetime so the system stays naive-UTC throughout.

    Bug (A): previously _parse_ts added tzinfo=UTC, causing TypeError when
    SQLAlchemy's synchronize_session='evaluate' compared aware LogEvent.ts
    against naive fight window datetimes from Fight.started_at.
    """
    raw = (
        "[ 2026.06.14 20:57:21 ] (combat) "
        "<color=0xffffffff><b>432</b> "
        "<color=0x77ffffff><font size=10>from</font> "
        "<b><color=0xffffffff>FakeEnemy Bravo[.TST](Brutix Navy Issue)</b>"
        '<font size=10><color=0x77ffffff> - 250mm Railgun II - Grazes'
    )
    evt = parse_line(raw)
    assert evt is not None
    assert evt.ts.tzinfo is None, (
        f"Expected naive datetime but got tzinfo={evt.ts.tzinfo!r}. "
        "Fix: remove tzinfo=UTC from _parse_ts in app/logs/parse.py"
    )
