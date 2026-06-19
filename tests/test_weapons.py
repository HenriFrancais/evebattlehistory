from app.analytics.weapons import classify_weapon


def test_railgun_is_hybrid_with_fallback():
    w = classify_weapon("250mm Railgun II")
    assert w.category == "hybrid"
    assert w.fallback_name == "250mm Railgun II"


def test_autocannon_is_projectile():
    assert classify_weapon("425mm AutoCannon II").category == "projectile"


def test_pulse_laser_is_laser():
    assert classify_weapon("Mega Pulse Laser II").category == "laser"


def test_rocket_before_missile():
    # 'rocket' must win over the generic 'missile' substring rule.
    assert classify_weapon("Rocket Launcher II").category == "rocket"


def test_heavy_missile_is_missile():
    assert classify_weapon("Heavy Missile Launcher II").category == "missile"


def test_smartbomb_before_bomb():
    assert classify_weapon("Large EMP Smartbomb II").category == "smartbomb"


def test_unknown_module_is_other_no_fallback():
    w = classify_weapon("Some Weird Faction Thing")
    assert w.category == "other"
    assert w.fallback_name is None


def test_none_module_is_other():
    assert classify_weapon(None).category == "other"
