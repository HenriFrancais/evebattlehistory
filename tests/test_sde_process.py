from app.sde.process import process_sde_lines, read_manifest_build


def test_read_manifest_build():
    assert read_manifest_build('{"buildNumber": 2812345, "releaseDate": "2026-06-01"}') == 2812345
    assert read_manifest_build("not json") is None


def test_process_filters_published_and_joins_groups():
    # CCP JSONL: one object per line. id may be "_key" or "typeID"; name may be {"en":..} or str.
    types = [
        '{"_key": 2488, "groupID": 53, "name": {"en": "Dual 150mm Railgun II"}, "published": true}',
        '{"_key": 999, "groupID": 53, "name": {"en": "Unpublished Thing"}, "published": false}',
        '{"typeID": 670, "groupID": 29, "name": {"en": "Capsule"}, "published": true}',
    ]
    groups = [
        '{"_key": 53, "categoryID": 7, "name": {"en": "Energy Weapon"}}',
        '{"_key": 29, "categoryID": 6, "name": {"en": "Capsule"}}',
    ]
    out = {r["type_id"]: r for r in process_sde_lines(types, groups)}
    assert 999 not in out  # unpublished dropped
    assert out[2488]["name"] == "Dual 150mm Railgun II"
    assert out[2488]["category_id"] == 7
    assert out[670]["category_id"] == 6 and out[670]["category_name"] == "Capsule"
