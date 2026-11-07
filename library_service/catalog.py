"""完整校验文件后发布不可变内存快照；加载失败不触碰当前快照或任何数据库。"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from decimal import Decimal
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Mapping

from jsonschema import Draft202012Validator

TYPES = ("world", "region", "place", "realm", "root", "item", "art", "faction", "character", "event")
DIRECTORIES = dict(
    zip(
        TYPES,
        (
            "world",
            "regions",
            "places",
            "realms",
            "roots",
            "items",
            "arts",
            "factions",
            "characters",
            "events",
        ),
    )
)
REFERENCE = re.compile(r"^(" + "|".join(TYPES) + r"):([a-z][a-z0-9]*(?:-[a-z0-9]+)*)$")
ANNOTATIONS = {"note", "duties", "side_effects"}
ROOT_COUNTS = {"heavenly": 1, "mutant": 1, "dual": 2, "triple": 3, "quad": 4, "penta": 5}


@dataclass(frozen=True)
class Issue:
    """只携带文件、字段和安全说明，不回显隐藏正文或令牌。"""

    file: str
    field: str
    message: str


class CatalogError(ValueError):
    """可恢复的资料校验失败；调用方必须保留旧快照。"""

    def __init__(self, issues: list[Issue]):
        self.issues = issues
        super().__init__("\n".join(f"{i.file}:{i.field}: {i.message}" for i in issues))


def fail(file: str, field: str, message: str) -> None:
    raise CatalogError([Issue(file, field, message)])


def pointer(parts) -> str:
    return "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def canonical(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()


def fingerprint(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("存在重复 JSON 字段")
        result[key] = value
    return result


def _constant(_):
    raise ValueError("不接受 NaN 或 Infinity")


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("数字超出有限范围")
    return result


def read_json(root: Path, relative: str, reads: dict[Path, bytes]):
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or "\\" in relative or not path.parts:
        fail(relative, "/", "资料路径必须位于配置目录内")
    candidate = root
    for part in path.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            fail(relative, "/", "不接受符号链接")
    try:
        if not candidate.resolve().is_relative_to(root.resolve()):
            fail(relative, "/", "资料路径越界")
        raw = candidate.read_bytes()
        reads[candidate] = raw
        return json.loads(
            raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant, parse_float=_float
        )
    except (OSError, UnicodeError, ValueError):
        fail(relative, "/", "无法读取合法 UTF-8 JSON（检查语法、重复字段和非有限数字）")


def validate_schema(record, schema, file: str) -> None:
    # 本仓库 Schema 自包含，不允许隐式网络检索。
    for error in Draft202012Validator(schema).iter_errors(record):
        fail(file, pointer(error.absolute_path), f"不满足 Schema 约束：{error.validator}")


def references(value, parts=()):
    if isinstance(value, dict):
        for key, child in value.items():
            if key not in ANNOTATIONS:
                yield from references(child, (*parts, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from references(child, (*parts, index))
    elif isinstance(value, str) and REFERENCE.fullmatch(value):
        yield value, pointer(parts)


@dataclass(frozen=True)
class Snapshot:
    """通过规范化字节保存数据；每次输出新对象，调用者不能修改内存事实。"""

    records: Mapping[str, bytes] = dataclass_field(repr=False)
    hashes: Mapping[str, str]
    by_type: Mapping[str, tuple[str, ...]]
    by_phase: Mapping[int, tuple[str, ...]]
    enabled_phase: int
    revision: str

    def entry(self, reference: str, *, public: bool) -> dict:
        record = json.loads(self.records[reference])
        if public:
            record = {key: record[key] for key in ("type", "key", "name", "phase", "public")}
        digest = self.hashes[reference]
        return {**record, "revision": f"sha256:{digest}", "content_hash": digest}

    def select(self, *, kind=None, phase=None, keys=None) -> list[str]:
        candidates = self.by_type[kind] if kind else tuple(self.records)
        if keys is not None:
            requested = set(keys)
            candidates = tuple(key for key in candidates if key in requested)
        if phase is not None:
            in_phase = set(self.by_phase.get(phase, ()))
            candidates = tuple(key for key in candidates if key in in_phase)
        return list(candidates)


def _cross_validate(records: dict[str, dict], files: dict[str, str]) -> None:
    worlds = [record for record in records.values() if record["type"] == "world"]
    if len(worlds) != 1:
        fail("manifest.json", "/categories/world", "必须且只能有一条世界资料")
    realms = [record for record in records.values() if record["type"] == "realm"]
    orders = sorted(record["rules"]["order"] for record in realms)
    if not orders or orders != list(range(len(orders))):
        fail("manifest.json", "/categories/realm", "境界 order 必须从 0 开始、连续且唯一")
    roots = [record for record in records.values() if record["type"] == "root"]
    if len(roots) != 6 or {r["rules"]["kind"] for r in roots} != set(ROOT_COUNTS):
        fail("manifest.json", "/categories/root", "必须提供六类不重复的灵根")
    if sum((Decimal(str(r["rules"]["probability"])) for r in roots), Decimal(0)) != Decimal(1):
        fail("manifest.json", "/categories/root", "灵根概率之和必须为 1.0")
    if any(record["phase"] != 1 for record in roots):
        fail("manifest.json", "/categories/root", "开局灵根基础分布必须在阶段一完整提供")

    for reference, record in records.items():
        file, rules = files[reference], record["rules"]
        for target, field in references(rules, ("rules",)):
            if target not in records:
                fail(file, field, "引用不存在")
            if records[target]["phase"] > record["phase"]:
                fail(file, field, "不能引用更晚阶段的资料")
        for value, field in _ranges(rules):
            low, high = (records[v]["rules"]["order"] for v in value)
            if low > high:
                fail(file, field, "境界区间上下限颠倒")
        if record["type"] == "realm":
            stages = [stage["key"] for stage in rules["stages"]]
            if len(stages) != len(set(stages)):
                fail(file, "/rules/stages", "小阶段 key 必须唯一")
            if set(rules["can"]) & set(rules["cannot"]):
                fail(file, "/rules/cannot", "能力不能同时允许和禁止")
        if record["type"] == "item" and rules["price"]["min"] > rules["price"]["max"]:
            fail(file, "/rules/price", "最低价格不能高于最高价格")
        if record["type"] == "root":
            for index, group in enumerate(rules["elements"]):
                allowed = (
                    {"wind", "thunder", "ice"}
                    if rules["kind"] == "mutant"
                    else {"metal", "wood", "water", "fire", "earth"}
                )
                if len(group) != ROOT_COUNTS[rules["kind"]] or not set(group) <= allowed:
                    fail(file, f"/rules/elements/{index}", "灵根种类与属性组合不符")
        if record["type"] == "character":
            stages = records[rules["realm"]]["rules"]["stages"]
            if rules["stage"] not in {stage["key"] for stage in stages}:
                fail(file, "/rules/stage", "小阶段不属于所引用境界")
        if record["type"] == "art" and rules["kind"] == "practice":
            if records[rules["realm"]]["rules"]["order"] > records[rules["max_realm"]]["rules"]["order"]:
                fail(file, "/rules/max_realm", "最高境界不能低于入门境界")
        if record["type"] == "place":
            seen = {reference}
            current = record
            while current["type"] == "place":
                parent = current["rules"]["parent"]
                if parent in seen:
                    fail(file, "/rules/parent", "地点树存在环")
                seen.add(parent)
                current = records[parent]
            if current["type"] != "region":
                fail(file, "/rules/parent", "地点必须最终归属于大域")
        if record["type"] == "event" and rules["trigger"]["phase"] != record["phase"]:
            fail(file, "/rules/trigger/phase", "事件触发阶段须与资料阶段一致")
        if record["type"] == "faction":
            recruited = [key for row in rules["recruit"] for key in row["roots"]]
            if len(recruited) != len(set(recruited)):
                fail(file, "/rules/recruit", "同一灵根不能匹配多条收徒规则")


def _ranges(value, parts=("rules",)):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "realm_range":
                yield child, pointer((*parts, key))
            elif isinstance(child, (dict, list)):
                yield from _ranges(child, (*parts, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _ranges(child, (*parts, index))


def load_catalog(data_root: Path, schema_root: Path, enabled_phase: int = 1) -> Snapshot:
    """校验所有阶段；只给已上线阶段建索引，整个过程不修改外部状态。"""
    reads: dict[Path, bytes] = {}
    schemas = {}
    for kind in (*TYPES, "manifest"):
        schema = read_json(schema_root, f"{kind}.json", reads)
        try:
            Draft202012Validator.check_schema(schema)
        except Exception:
            fail(f"schema/{kind}.json", "/", "Schema 本身不合法")
        # 自包含 Schema 禁止网络或任意文件引用。
        if b'"$ref"' in canonical(schema) or b'"$dynamicRef"' in canonical(schema):
            fail(f"schema/{kind}.json", "/", "Schema 必须自包含，不接受外部引用")
        schemas[kind] = schema
    manifest = read_json(data_root, "manifest.json", reads)
    validate_schema(manifest, schemas["manifest"], "manifest.json")
    if not 1 <= enabled_phase <= manifest["published_phase"]:
        fail("manifest.json", "/published_phase", "不能启用未上线的内容阶段")
    records, files, paths = {}, {}, set()
    for kind in TYPES:
        for path in manifest["categories"][kind]:
            if path in paths:
                fail("manifest.json", f"/categories/{kind}", "文件不能重复登记")
            paths.add(path)
            record = read_json(data_root, path, reads)
            validate_schema(record, schemas[kind], path)
            expected = (
                f"items/{record['rules']['category']}/{record['key']}.json"
                if kind == "item"
                else f"{DIRECTORIES[kind]}/{record['key']}.json"
            )
            if path != expected:
                fail(path, "/key", "文件路径须与类别、物品分类和 key 一致")
            reference = f"{kind}:{record['key']}"
            if reference in records:
                fail(path, "/key", "同类别 key 重复")
            records[reference], files[reference] = record, path
    actual = {
        p.relative_to(data_root).as_posix()
        for p in data_root.rglob("*.json")
        if p.relative_to(data_root).as_posix() != "manifest.json"
    }
    if actual != paths:
        fail("manifest.json", "/categories", "目录内 JSON 文件与清单不一致")
    _cross_validate(records, files)
    active = {key: record for key, record in sorted(records.items()) if record["phase"] <= enabled_phase}
    if not any(record["type"] == "world" for record in active.values()):
        fail("manifest.json", "/categories/world", "启用阶段必须包含世界资料")
    for path, raw in reads.items():
        try:
            if path.read_bytes() != raw:
                fail(path.name, "/", "加载期间文件发生变化，请完成 Git 更新后重试")
        except OSError:
            fail(path.name, "/", "加载期间文件不可读")
    hashes = {key: fingerprint(record) for key, record in active.items()}
    return Snapshot(
        records=MappingProxyType({key: canonical(record) for key, record in active.items()}),
        hashes=MappingProxyType(hashes),
        by_type=MappingProxyType(
            {kind: tuple(key for key, r in active.items() if r["type"] == kind) for kind in TYPES}
        ),
        by_phase=MappingProxyType(
            {
                phase: tuple(key for key, r in active.items() if r["phase"] == phase)
                for phase in range(1, enabled_phase + 1)
            }
        ),
        enabled_phase=enabled_phase,
        revision="sha256:" + fingerprint({"entries": hashes, "enabled_phase": enabled_phase}),
    )
