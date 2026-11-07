"""用破坏性反例验证文件、引用、阶段和跨字段约束；不以样例通过代替校验。"""

import copy
import json

import pytest

from library_service.api import ROOT
from library_service.catalog import CatalogError, load_catalog
from tests.conftest import record, write


def load(data, phase=1):
    return load_catalog(data, ROOT / "schema", phase)


def edit(data, path, mutate):
    file = data / path
    value = json.loads(file.read_text())
    mutate(value)
    file.write_text(json.dumps(value, ensure_ascii=False))


def test_all_ten_categories_and_detached_outputs(complete):
    snapshot = load(complete)
    assert len(snapshot.records) == 16
    assert all(snapshot.by_type[kind] for kind in snapshot.by_type)
    value = snapshot.entry("character:guide", public=False)
    value["secret"]["goal"] = "changed"
    assert snapshot.entry("character:guide", public=False)["secret"]["goal"] != "changed"


@pytest.mark.parametrize(
    "path,mutation",
    [
        ("items/spirit_stone/spirit-stone.json", lambda r: r.update(unknown=True)),
        ("items/spirit_stone/spirit-stone.json", lambda r: r["public"].update(secret="泄露")),
        ("items/spirit_stone/spirit-stone.json", lambda r: r["rules"].update(grade="tier_1")),
        ("items/spirit_stone/spirit-stone.json", lambda r: r["rules"]["effect"].update(type="heal")),
        ("items/spirit_stone/spirit-stone.json", lambda r: r["rules"]["price"].update(min=100, max=1)),
        ("items/spirit_stone/spirit-stone.json", lambda r: r["rules"]["price"].update(currency="silver")),
        ("items/spirit_stone/spirit-stone.json", lambda r: r["rules"].update(found_in=["realm:lianqi"])),
        ("items/spirit_stone/spirit-stone.json", lambda r: r["rules"].update(found_in=["place:missing"])),
        ("items/spirit_stone/spirit-stone.json", lambda r: r.update(type="realm")),
        ("items/spirit_stone/spirit-stone.json", lambda r: r.update(key="renamed")),
        ("realms/lianqi.json", lambda r: r["rules"]["stages"].append({"key": "l1", "name": "另一个一层"})),
        ("realms/lianqi.json", lambda r: r["rules"].update(can=[])),
        ("realms/lianqi.json", lambda r: r["rules"].update(cannot=[])),
        ("realms/lianqi.json", lambda r: r["rules"].update(order=0)),
        ("realms/lianqi.json", lambda r: r["rules"].update(order=2)),
        ("realms/lianqi.json", lambda r: r["rules"]["can"].append("weapon_flight")),
        ("regions/zhongzhou.json", lambda r: r["rules"].update(realm_range=["realm:lianqi", "realm:fanren"])),
        ("roots/heavenly.json", lambda r: r["rules"].update(probability=0.02)),
        ("roots/heavenly.json", lambda r: r["rules"].update(elements=[["wood", "water"]])),
        ("roots/heavenly.json", lambda r: r["rules"].update(elements=[["wind"]])),
        ("roots/mutant.json", lambda r: r["rules"].update(elements=[["wood"]])),
        ("roots/mutant.json", lambda r: r.update(phase=2)),
        ("places/qingxi-town.json", lambda r: r["rules"].update(parent="place:qingxi-town")),
        ("places/qingxi-town.json", lambda r: r["rules"].update(parent="item:spirit-stone")),
        ("places/qingxi-town.json", lambda r: r.update(phase=2)),
        ("manifest.json", lambda r: r["categories"]["world"].clear()),
        ("manifest.json", lambda r: r["categories"]["root"].pop()),
        ("manifest.json", lambda r: r["categories"]["root"].append(r["categories"]["root"][0])),
    ],
)
def test_invalid_data_rejected_with_file_and_field(data, path, mutation):
    edit(data, path, mutation)
    with pytest.raises(CatalogError) as caught:
        load(data)
    issue = caught.value.issues[0]
    assert issue.file and issue.field.startswith("/")


def test_multi_place_cycle(data):
    first = json.loads((data / "places/qingxi-town.json").read_text())
    second = copy.deepcopy(first)
    second["key"] = "other-town"
    second["rules"]["parent"] = "place:qingxi-town"
    first["rules"]["parent"] = "place:other-town"
    write(data, "place", first)
    write(data, "place", second)
    with pytest.raises(CatalogError, match="存在环"):
        load(data)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r["rules"].update(stage="missing"),
        lambda r: r["rules"].update(realm="item:spirit-stone"),
    ],
)
def test_character_stage_and_reference_category(complete, mutation):
    edit(complete, "characters/guide.json", mutation)
    with pytest.raises(CatalogError):
        load(complete)


def test_hidden_schema_errors_do_not_echo_values(data):
    edit(data, "places/qingxi-town.json", lambda r: r["secret"].update(unexpected="MUST-NOT-LEAK"))
    with pytest.raises(CatalogError) as caught:
        load(data)
    assert "MUST-NOT-LEAK" not in str(caught.value)
    assert "/secret" in str(caught.value)


def test_future_entries_are_validated_but_not_indexed(data):
    future = record(
        "realm",
        "future-realm",
        {
            "order": 2,
            "detail": "placeholder",
            "stages": [{"key": "early", "name": "初期"}],
            "lifespan_years": 200,
            "can": [],
            "cannot": [],
        },
        phase=2,
        public={"description": "未来占位", "usage": "未开放", "limitations": "不可使用"},
    )
    write(data, "realm", future)
    assert "realm:future-realm" not in load(data).records
    with pytest.raises(CatalogError, match="未上线"):
        load(data, 2)
    edit(data, "manifest.json", lambda r: r.update(published_phase=2))
    assert "realm:future-realm" in load(data, 2).records
    edit(data, "realms/future-realm.json", lambda r: r["rules"].update(can=["not-a-capability"]))
    with pytest.raises(CatalogError):
        load(data)


def test_formatting_does_not_change_fingerprint(data):
    before = load(data)
    for path in data.rglob("*.json"):
        path.write_text(json.dumps(json.loads(path.read_text()), ensure_ascii=True, sort_keys=True))
    after = load(data)
    assert before.revision == after.revision
    edit(data, "places/qingxi-town.json", lambda r: r["secret"].update(notes="修订后的内容"))
    assert load(data).hashes["place:qingxi-town"] != before.hashes["place:qingxi-town"]


@pytest.mark.parametrize("raw", ['{"key":"a","key":"b"}', '{"n":NaN}', '{"n":Infinity}', '{"n":1e999}', "{"])
def test_invalid_json_rejected(data, raw):
    (data / "realms/lianqi.json").write_text(raw)
    with pytest.raises(CatalogError):
        load(data)


def test_symlink_and_traversal_rejected(data, tmp_path):
    original = data / "realms/lianqi.json"
    outside = tmp_path / "outside.json"
    outside.write_bytes(original.read_bytes())
    original.unlink()
    original.symlink_to(outside)
    with pytest.raises(CatalogError, match="符号链接"):
        load(data)
    edit(data, "manifest.json", lambda r: r["categories"]["realm"].append("../outside.json"))
    original.unlink()
    original.write_bytes(outside.read_bytes())
    with pytest.raises(CatalogError, match="目录内"):
        load(data)


def test_unlisted_json_is_not_silently_ignored(data):
    (data / "orphan.json").write_text("{}")
    with pytest.raises(CatalogError, match="清单不一致"):
        load(data)


def test_nested_manifest_cannot_hide_from_inventory(data):
    (data / "places/manifest.json").write_text("{}")
    with pytest.raises(CatalogError, match="清单不一致"):
        load(data)


def test_duplicate_world(data):
    world = json.loads((data / "world/yunheng-world.json").read_text())
    world["key"] = "other-world"
    write(data, "world", world)
    with pytest.raises(CatalogError, match="只能有一条"):
        load(data)


def test_root_distribution_requires_all_six_kinds(data):
    (data / "roots/mutant.json").unlink()
    edit(data, "manifest.json", lambda r: r["categories"]["root"].remove("roots/mutant.json"))
    with pytest.raises(CatalogError, match="六类"):
        load(data)


def test_schema_cannot_fetch_external_resources(data, tmp_path):
    import shutil

    schemas = tmp_path / "schema"
    shutil.copytree(ROOT / "schema", schemas)
    schema = json.loads((schemas / "item.json").read_text())
    schema["$ref"] = "https://example.invalid/schema"
    (schemas / "item.json").write_text(json.dumps(schema))
    with pytest.raises(CatalogError, match="不接受外部引用"):
        load_catalog(data, schemas)


def test_event_and_recruit_consistency(complete):
    edit(complete, "events/sample-event.json", lambda r: r["rules"]["trigger"].update(phase=2))
    with pytest.raises(CatalogError, match="触发阶段"):
        load(complete)
