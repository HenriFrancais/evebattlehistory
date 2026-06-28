from app.logs.entity import split_entity

SHIPS = frozenset(
    {"Guardian", "Scorpion", "Tempest Fleet Issue", "Bhaalgorn", "Arithmos Tyrannos",
     "Legion", "Devoter", "Nestor", "Sabre", "Proteus", "Leshak"}
)


def test_pilot_in_trailing_square_bracket():
    # "ShipType [ALLI] [CORP] [PilotName]" overview layout (seen on remote-rep
    # targets) — the pilot is the last square-bracket group; tickers are the
    # earlier <=5-char all-caps ones.
    assert split_entity("Sabre [NV] [NVACA] [Francis the Mute]", SHIPS) == (
        "Francis the Mute", "Sabre",
    )
    assert split_entity("Bhaalgorn [ECHO.] [INOU] [Outalized]", SHIPS) == (
        "Outalized", "Bhaalgorn",
    )


def test_short_lowercase_pilot_in_angle():
    # A 5-char pilot name with lowercase letters (e.g. "Ch1pz", "Tom-w") must be
    # recovered, not mistaken for an all-caps <=5 char alliance ticker.
    assert split_entity("Nestor [ECHO.] <Ch1pz>", SHIPS) == ("Ch1pz", "Nestor")
    assert split_entity("Legion [LUPUS] &lt;Tom-w&gt;", SHIPS) == ("Tom-w", "Legion")


def test_pilot_in_angle_brackets():
    # "ShipType [TICKER] <PilotName>" overview layout — the pilot sits inside the
    # angle brackets that _clean would otherwise discard. Recover it.
    assert split_entity("Legion [NV] <Ra'zok Zateki>", SHIPS) == ("Ra'zok Zateki", "Legion")


def test_pilot_in_angle_brackets_html_encoded():
    assert split_entity("Tempest Fleet Issue [URSA] &lt;Triffnixxx&gt;", SHIPS) == (
        "Triffnixxx", "Tempest Fleet Issue",
    )


def test_angle_alliance_ticker_not_taken_as_pilot():
    # Ship-only line whose only angle group is a short alliance ticker (<=5 chars,
    # no space) must NOT be mistaken for a pilot name.
    assert split_entity("Devoter [URSA] <NV>", SHIPS) == (None, "Devoter")


def test_player_ship_prefix():
    # "ShipType CharacterName [CORP] <ALLI>" → split on the leading ship token
    assert split_entity("Guardian Jennifer Hibra [NVACA] <NV>", SHIPS) == (
        "Jennifer Hibra", "Guardian",
    )


def test_multiword_ship_prefix():
    assert split_entity("Tempest Fleet Issue Bob Smith [X] <Y>", SHIPS) == (
        "Bob Smith", "Tempest Fleet Issue",
    )


def test_html_encoded_tickers_stripped():
    assert split_entity("Guardian Faith Hibra [NVACA] &lt;NV&gt;", SHIPS) == (
        "Faith Hibra", "Guardian",
    )


def test_new_encoding_ship_suffix():
    # "CharacterName [CORP][ALLI] ShipType" → ship is the trailing token
    assert split_entity("Alan Bell [URSA][URSA.] Scorpion", SHIPS) == ("Alan Bell", "Scorpion")


def test_npc_bare_name():
    # Whole cleaned string is itself an entity name → NPC, no character
    assert split_entity("Arithmos Tyrannos", SHIPS) == (None, "Arithmos Tyrannos")
    assert split_entity("Bhaalgorn", SHIPS) == (None, "Bhaalgorn")


def test_unknown_no_ship():
    assert split_entity("Totally Unknown Pilot [X]", SHIPS) == ("Totally Unknown Pilot", None)


def test_empty():
    assert split_entity("", SHIPS) == (None, None)


def test_trailing_dash_recovers_pilot_from_bracket():
    assert split_entity("Proteus [NV] [NVACA] [Nate Marston] -", SHIPS) == (
        "Nate Marston", "Proteus"
    )
    assert split_entity("Leshak [LUPUS] [OMGGF] [Tom-w] -", SHIPS) == (
        "Tom-w", "Leshak"
    )


def test_correct_ship_pilot_swap_fixes_ship_first_overview():
    from app.logs.entity import correct_ship_pilot_swap
    # Parser assumed NEW (pilot-first) but the overview was "Ship [CORP] Pilot":
    # name="Leshak" (a ship), ship="Dread PiIot Roberts" (a pilot) → swap.
    assert correct_ship_pilot_swap("Leshak", "Dread PiIot Roberts", SHIPS) == (
        "Dread PiIot Roberts", "Leshak",
    )
    # Correct NEW assignment is untouched (trailing token is the known ship).
    assert correct_ship_pilot_swap("Mustard Appreciator", "Leshak", SHIPS) == (
        "Mustard Appreciator", "Leshak",
    )
    # No ship known either side → no change.
    assert correct_ship_pilot_swap("Alice", "Bob", SHIPS) == ("Alice", "Bob")
