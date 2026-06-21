from app.logs.entity import split_entity

SHIPS = frozenset(
    {"Guardian", "Scorpion", "Tempest Fleet Issue", "Bhaalgorn", "Arithmos Tyrannos",
     "Legion", "Devoter"}
)


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
