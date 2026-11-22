"""核对实际发布目录中已确认的首批规则，覆盖测试样例不能代替的内容约定。"""

import pytest

from library_service.api import ROOT
from library_service.catalog import load_catalog
from library_service.migrate import migrate


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(ROOT / "data", ROOT / "schema", 1)


def test_all_realm_names_available_but_only_first_three_define_progression(catalog):
    realms = [catalog.entry(key, public=False)["rules"] for key in catalog.by_type["realm"]]
    realms.sort(key=lambda rules: rules["order"])
    assert [rules["order"] for rules in realms] == list(range(10))
    assert all(rules["detail"] == "full" for rules in realms[:3])
    assert all(rules == {"order": rules["order"], "detail": "placeholder"} for rules in realms[3:])


def test_manual_is_obtained_through_teaching_or_story(catalog):
    manual = catalog.entry("item:entry-breathing-manual", public=False)["rules"]
    art = catalog.entry("art:basic-breathing", public=False)["rules"]
    assert set(manual["obtain"]) == set(art["obtain"]) == {"sect", "reward"}
    assert "price" not in manual and not manual.get("found_in_kinds")
    for place in manual["found_in"]:
        assert catalog.entry(place, public=False)["rules"]["kind"] == "sect_gate"
    for key in catalog.by_type["place"]:
        location = catalog.entry(key, public=False)["rules"]
        if location["kind"] != "sect_gate":
            assert "item:entry-breathing-manual" not in location["products"]


def test_currency_and_apothecary_stock(catalog):
    stone = catalog.entry("item:mid-spirit-stone", public=False)["rules"]
    assert stone["price"] == {"min": 100, "max": 100, "currency": "spirit_stone", "unit": "low_spirit_stone"}
    shop = "place:hesheng-apothecary"
    products = catalog.entry(shop, public=False)["rules"]["products"]
    assert products
    for ref in products:
        rules = catalog.entry(ref, public=False)["rules"]
        assert rules["price"]["unit"] == "silver_liang" and shop in rules["found_in"]


def test_real_starter_preserves_place_type_semantics(tmp_path, catalog):
    result = migrate(
        ROOT / "tests/fixtures/legacy/starter-v0.1.json",
        ROOT / "data",
        ROOT / "content-review/legacy-starter-map.json",
        tmp_path / "migrated",
        ROOT / "schema",
    )
    assert result["revision"] == catalog.revision
    assert len(result["entries"]) == 19
    kinds = {key: row["kind"] for key, row in result["entries"].items() if row["action"] == "place_kind"}
    assert kinds == {
        "place_types:cultivator-market": "market",
        "place_types:herb-garden": "herb_garden",
        "place_types:quiet-chamber": "quiet_room",
    }
