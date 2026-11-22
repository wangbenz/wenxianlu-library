"""所有变更只针对测试复制目录，不访问本地资料数据库。"""

import json
import shutil

import pytest

from library_service.api import ROOT, Settings


def write(root, kind, record):
    plural = {
        "place": "places",
        "realm": "realms",
        "root": "roots",
        "art": "arts",
        "faction": "factions",
        "character": "characters",
        "event": "events",
        "region": "regions",
    }
    path = (
        f"items/{record['rules']['category']}/{record['key']}.json"
        if kind == "item"
        else f"{plural.get(kind, kind)}/{record['key']}.json"
    )
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    manifest = json.loads((root / "manifest.json").read_text())
    if path not in manifest["categories"][kind]:
        manifest["categories"][kind].append(path)
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))
    return target


def record(kind, key, rules, *, phase=1, public=None, narration=None, secret=None):
    return {
        "type": kind,
        "key": key,
        "name": "测试资料",
        "phase": phase,
        "public": public if public is not None else {"description": "测试说明"},
        "rules": rules,
        "narration": narration or {},
        "secret": secret or {},
    }


@pytest.fixture
def data(tmp_path):
    root = tmp_path / "data"
    shutil.copytree(ROOT / "data", root)
    return root


@pytest.fixture
def config(data):
    return Settings(data, ROOT / "schema", 1, "test-service-" + "a" * 32, "test-reload-" + "b" * 32)


@pytest.fixture
def complete(data):
    write(
        data,
        "art",
        record(
            "art",
            "entry-art",
            {
                "kind": "practice",
                "grade": "huang_low",
                "realm": "realm:lianqi",
                "max_realm": "realm:lianqi",
                "affinity": ["wood"],
                "effect": {"type": "utility", "note": "测试功法"},
                "obtain": ["sect"],
            },
            public={"description": "测试功法", "usage": "学习吐纳", "limitations": "仅为测试"},
        ),
    )
    write(
        data,
        "faction",
        record(
            "faction",
            "sample-sect",
            {
                "kind": "sect",
                "seat": "place:qingxi-town",
                "strongest_realm": "realm:lianqi",
                "recruit": [{"roots": ["root:heavenly"], "rank": "inner"}],
                "ranks": {
                    "inner": {"monthly": [{"item": "item:spirit-stone", "quantity": 1}], "duties": "测试职责"}
                },
                "arts": ["art:entry-art"],
                "specialties": ["item:spirit-stone"],
                "characters": ["character:guide"],
                "visit_weight": 1,
            },
            narration={"style": "测试门风"},
            secret={"notes": "门派隐藏内容"},
        ),
    )
    write(
        data,
        "character",
        record(
            "character",
            "guide",
            {
                "realm": "realm:lianqi",
                "stage": "l1",
                "faction": "faction:sample-sect",
                "location": "place:qingxi-town",
                "holdings": ["item:spirit-stone"],
                "role": "guide",
            },
            public={"description": "引路者", "identity": "公开身份"},
            narration={"personality": "沉静", "speech": "简短"},
            secret={"hidden_identity": "不可泄露的身份", "goal": "不可泄露的目标"},
        ),
    )
    write(
        data,
        "event",
        record(
            "event",
            "sample-event",
            {
                "pool": "qingxi-before-recruit",
                "weight": 1,
                "trigger": {
                    "places": ["place:qingxi-town"],
                    "realm_range": ["realm:fanren", "realm:lianqi"],
                    "phase": 1,
                },
                "involves": {"characters": ["character:guide"], "items": ["item:spirit-stone"]},
                "reward_cap": {"max_rarity": "common", "max_items": 1},
            },
            public={},
            narration={"setup": "测试开场", "outcomes": ["一种可能走向"]},
            secret={"truth": "不可泄露的真相"},
        ),
    )
    return data
