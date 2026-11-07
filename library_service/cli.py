"""提交前文件校验命令；只读资料，失败返回非零，不访问数据库或网络。"""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .api import ROOT
from .catalog import CatalogError, load_catalog


def main():
    parser = argparse.ArgumentParser(description="校验 JSON 文件资料库")
    parser.add_argument("--data-root", type=Path, default=ROOT / "data")
    parser.add_argument("--schema-root", type=Path, default=ROOT / "schema")
    parser.add_argument("--enabled-phase", type=int, default=1)
    args = parser.parse_args()
    try:
        snapshot = load_catalog(args.data_root, args.schema_root, args.enabled_phase)
    except CatalogError as error:
        print(json.dumps({"valid": False, "issues": [asdict(i) for i in error.issues]}, ensure_ascii=False))
        return 1
    print(
        json.dumps(
            {
                "valid": True,
                "entries": len(snapshot.records),
                "revision": snapshot.revision,
                "content_hash": snapshot.revision.removeprefix("sha256:"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
