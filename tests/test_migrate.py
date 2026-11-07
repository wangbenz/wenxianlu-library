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
