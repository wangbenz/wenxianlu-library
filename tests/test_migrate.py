"""迁移需要明确映射；已有输出、遗漏旧条目和错误替代均不能静默覆盖。"""

import json

import pytest

from library_service.api import ROOT
from library_service.catalog import CatalogError, fingerprint, load_catalog
from library_service.migrate import migrate


def plan(tmp_path, rows):
    source = tmp_path / "legacy.json"
    old = {"format_version": 1, "realms": [{"key": "lianqi"}], "items": []}
    source.write_text(json.dumps(old))
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps({"source_hash": fingerprint(old), "entries": rows}))
    return source, mapping


def test_migration_only_copies_reviewed_manifest(data, tmp_path):
    source, mapping = plan(
        tmp_path, {"realms:lianqi": {"action": "replace", "target": "realm:lianqi", "reason": "已审核重写"}}
    )
    (data / ".env").write_text("not-for-export")
    output = tmp_path / "new-data"
    original = source.read_bytes()
    result = migrate(source, data, mapping, output, ROOT / "schema")
    assert not (output / ".env").exists()
    assert source.read_bytes() == original
    assert load_catalog(output, ROOT / "schema").revision == result["revision"]
    with pytest.raises(CatalogError, match="禁止覆盖"):
        migrate(source, data, mapping, output, ROOT / "schema")


@pytest.mark.parametrize(
    "entries",
    [
        {},
        {"realms:lianqi": {"action": "replace", "target": "realm:missing", "reason": "测试"}},
        {"realms:lianqi": {"action": "replace", "target": "item:spirit-stone", "reason": "错误类别"}},
        {"realms:lianqi": {"action": "replace", "target": [], "reason": "错误类型"}},
        {"realms:lianqi": {"action": "retire", "reason": ""}},
        {"realms:lianqi": {"action": "guess", "reason": "测试"}},
    ],
)
def test_migration_rejects_unreviewed_changes(data, tmp_path, entries):
    source, mapping = plan(tmp_path, entries)
    output = tmp_path / "new-data"
    with pytest.raises(CatalogError):
        migrate(source, data, mapping, output, ROOT / "schema")
    assert not output.exists()


def test_retirement_requires_explicit_review(data, tmp_path):
    source, mapping = plan(
        tmp_path, {"realms:lianqi": {"action": "retire", "reason": "测试用的明确退出决定"}}
    )
    result = migrate(source, data, mapping, tmp_path / "new-data", ROOT / "schema")
    assert result["entries"]["realms:lianqi"]["action"] == "retire"


def test_legacy_fixture_must_have_complete_mapping(data, tmp_path):
    legacy = ROOT / "tests/fixtures/legacy/starter-v0.1.json"
    payload = json.loads(legacy.read_text())
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps({"source_hash": fingerprint(payload), "entries": {}}))
    with pytest.raises(CatalogError, match="逐条覆盖"):
        migrate(legacy, data, mapping, tmp_path / "new-data", ROOT / "schema")


@pytest.mark.parametrize("kind", ["market", "herb_garden", "quiet_room", "unknown"])
def test_place_type_maps_to_a_kind_without_selecting_an_instance(data, tmp_path, kind):
    legacy = {"format_version": 1, "place_types": [{"key": "legacy-kind"}]}
    source, mapping = tmp_path / "legacy.json", tmp_path / "mapping.json"
    source.write_text(json.dumps(legacy))
    entries = {"place_types:legacy-kind": {"action": "place_kind", "kind": kind, "reason": "保留场所类型"}}
    mapping.write_text(json.dumps({"source_hash": fingerprint(legacy), "entries": entries}))
    if kind == "unknown":
        with pytest.raises(CatalogError, match="有效的地点"):
            migrate(source, data, mapping, tmp_path / "out", ROOT / "schema")
    else:
        report = migrate(source, data, mapping, tmp_path / "out", ROOT / "schema")
        assert report["entries"] == entries
