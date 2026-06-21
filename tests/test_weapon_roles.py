from app.analytics.weapon_roles import WeaponTypeInfo, weapon_role


def test_railgun_is_turret():
    info = WeaponTypeInfo(3074, "250mm Railgun II", "Hybrid Weapon", 7)
    assert weapon_role(info).role == "turret"


def test_heavy_missile_is_missile():
    info = WeaponTypeInfo(2410, "Heavy Missile Launcher II", "Missile Launcher Heavy", 7)
    assert weapon_role(info).role == "missile"


def test_drone_by_category_18():
    assert weapon_role(WeaponTypeInfo(2486, "Warrior II", "Combat Drone", 18)).role == "drone"


def test_smartbomb():
    info = WeaponTypeInfo(0, "Large EMP Smartbomb II", "Smart Bomb", 7)
    assert weapon_role(info).role == "smartbomb"


def test_scram_is_tackle():
    info = WeaponTypeInfo(0, "Warp Scrambler II", "Warp Scrambler", 7)
    assert weapon_role(info).role == "tackle"


def test_web_is_tackle():
    info = WeaponTypeInfo(0, "Stasis Webifier II", "Stasis Web", 7)
    assert weapon_role(info).role == "tackle"


def test_ecm_is_ewar():
    assert weapon_role(WeaponTypeInfo(0, "Multispectral ECM II", "ECM", 7)).role == "ewar"


def test_unknown_is_other():
    assert weapon_role(WeaponTypeInfo(0, "Some Weird Thing", "Mystery", 0)).role == "other"
