"""将旧 JSON 明确映射到已审核的新文件集；拒绝猜测硬规则，不连接旧数据库。"""

import argparse
import shutil
from pathlib import Path

from .api import ROOT
from .catalog import CatalogError, canonical, fail, fingerprint, load_catalog, read_json

LEGACY_TYPES = {"realms", "items", "arts", "place_types", "facility_types", "recipes"}
TARGET_TYPES = {
    "realms": "realm",
    "items": "item",
    "arts": "art",
    "place_types": "place",
    "facility_types": "place",
}


def draft_plan(legacy: Path) -> dict:
    """只列出需要人工审核的旧编号；不凭旧说明推断新规则。"""
    old = read_json(legacy.parent, legacy.name, {})
    if not isinstance(old, dict) or type(old.get("format_version")) is not int or old["format_version"] != 1:
        fail(legacy.name, "/format_version", "只接受旧整数格式 1 的 JSON")
    if set(old) - (LEGACY_TYPES | {"format_version"}):
        fail(legacy.name, "/", "旧文件包含未知分类")
    entries = {}
    for kind, rows in old.items():
        if kind == "format_version":
            continue
        if not isinstance(rows, list):
            fail(legacy.name, f"/{kind}", "旧分类必须是数组")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("key"), str):
                fail(legacy.name, f"/{kind}", "旧条目缺少 key")
            reference = f"{kind}:{row['key']}"
            if reference in entries:
                fail(legacy.name, f"/{kind}", "旧编号重复")
            entries[reference] = {"action": "replace", "target": None, "reason": ""}
    return {"source_hash": fingerprint(old), "entries": entries}


def migrate(legacy: Path, reviewed_data: Path, mapping: Path, output: Path, schema_root: Path) -> dict:
    """先验证完整映射和新资料；只输出到全新目录，源 JSON 与任何数据库均不改写。"""
    old = read_json(legacy.parent, legacy.name, {})
    plan = read_json(mapping.parent, mapping.name, {})
    if not isinstance(old, dict) or type(old.get("format_version")) is not int or old["format_version"] != 1:
        fail(legacy.name, "/format_version", "只接受旧整数格式 1 的 JSON 导入文件")
    if set(old) - (LEGACY_TYPES | {"format_version"}):
        fail(legacy.name, "/", "旧文件包含未知分类")
    if (
        not isinstance(plan, dict)
        or set(plan) != {"source_hash", "entries"}
        or not isinstance(plan["entries"], dict)
    ):
        fail(mapping.name, "/", "映射须包含 source_hash 与 entries")
    if plan["source_hash"] != fingerprint(old):
        fail(mapping.name, "/source_hash", "旧资料内容与审阅的来源指纹不一致")
    keys = set()
    for kind, rows in old.items():
        if kind == "format_version":
            continue
        if not isinstance(rows, list):
            fail(legacy.name, f"/{kind}", "旧分类必须是数组")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("key"), str):
                fail(legacy.name, f"/{kind}", "旧条目缺少 key")
            reference = f"{kind}:{row['key']}"
            if reference in keys:
                fail(legacy.name, f"/{kind}", "旧编号重复")
            keys.add(reference)
    if set(plan["entries"]) != keys:
        fail(mapping.name, "/entries", "必须逐条覆盖旧资料，不能静默丢弃或添加映射")
    manifest = read_json(reviewed_data, "manifest.json", {})
    snapshot = load_catalog(reviewed_data, schema_root, manifest.get("published_phase", 1))
    for reference, action in plan["entries"].items():
        if (
            not isinstance(action, dict)
            or not isinstance(action.get("reason"), str)
            or not action["reason"].strip()
        ):
            fail(mapping.name, f"/entries/{reference}", "每项必须说明迁移或退出的原因")
        if action.get("action") == "replace" and set(action) == {"action", "target", "reason"}:
            if not isinstance(action["target"], str) or action["target"] not in snapshot.records:
                fail(mapping.name, f"/entries/{reference}/target", "替代资料不存在或尚未上线")
            expected = TARGET_TYPES.get(reference.split(":", 1)[0])
            if not expected or action["target"].split(":", 1)[0] != expected:
                fail(
                    mapping.name,
                    f"/entries/{reference}/target",
                    "迁移目标类别不符；无对应类别的旧条目须明确退出或另行设计",
                )
        elif not (action.get("action") == "retire" and set(action) == {"action", "reason"}):
            fail(mapping.name, f"/entries/{reference}", "必须显式选择 replace 或 retire")
    if output.exists() or output.is_symlink():
        fail(str(output), "/", "输出目录已存在，禁止覆盖")
    if output.resolve().is_relative_to(reviewed_data.resolve()):
        fail(str(output), "/", "输出不能位于输入资料目录内")
    # 只复制清单内资料，不复制同目录的 .env、日志或数据库。
    output.mkdir(parents=True, exist_ok=False)
    for path in ["manifest.json", *[path for paths in manifest["categories"].values() for path in paths]]:
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(reviewed_data / path, target)
    result = load_catalog(output, schema_root, manifest["published_phase"])
    if result.revision != snapshot.revision:
        fail(str(output), "/", "复制期间资料改变；输出未激活，请检查后重新生成")
    return {
        "source_hash": fingerprint(old),
        "revision": result.revision,
        "entries": plan["entries"],
        "output": str(output),
    }


def main():
    parser = argparse.ArgumentParser(description="显式迁移旧 JSON 到已审核的文件集，不读取数据库")
    parser.add_argument("--legacy", type=Path, required=True)
    parser.add_argument("--plan", action="store_true", help="只生成待审核映射，不输出资料目录")
    parser.add_argument("--reviewed-data", type=Path)
    parser.add_argument("--mapping", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--schema-root", type=Path, default=ROOT / "schema")
    args = parser.parse_args()
    try:
        if args.plan:
            result = draft_plan(args.legacy)
        else:
            if any(value is None for value in (args.reviewed_data, args.mapping, args.output)):
                parser.error("执行迁移需要 --reviewed-data、--mapping 与 --output")
            result = migrate(args.legacy, args.reviewed_data, args.mapping, args.output, args.schema_root)
    except (CatalogError, OSError) as error:
        print(str(error))
        return 1
    print(canonical(result).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
