"""分层只读接口与受保护的重载；不做角色判定，不保存资料，不读取数据库。"""

from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import RLock

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from .catalog import REFERENCE, TYPES, CatalogError, Snapshot, load_catalog

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    """服务端配置；令牌仅来自进程环境，不回显、不写文件。"""

    data_root: Path
    schema_root: Path
    enabled_phase: int
    service_token: str = field(repr=False)
    reload_token: str = field(repr=False)

    @classmethod
    def from_env(cls):
        return cls(
            Path(os.environ.get("LIBRARY_DATA_ROOT", ROOT / "data")),
            Path(os.environ.get("LIBRARY_SCHEMA_ROOT", ROOT / "schema")),
            int(os.environ.get("LIBRARY_ENABLED_PHASE", "1")),
            os.environ.get("LIBRARY_SERVICE_TOKEN", ""),
            os.environ.get("LIBRARY_RELOAD_TOKEN", ""),
        )

    def validate(self):
        if min(len(self.service_token), len(self.reload_token)) < 32:
            raise ValueError("服务间令牌与重载令牌均须至少 32 字符")
        if any(
            not token.isascii() or any(c.isspace() for c in token)
            for token in (self.service_token, self.reload_token)
        ):
            raise ValueError("令牌须为不含空白的 ASCII 随机串")
        if hmac.compare_digest(self.service_token, self.reload_token):
            raise ValueError("服务间令牌与重载令牌必须不同")


class CatalogStore:
    """串行构建候选快照，仅在完整成功后原子替换；读取始终固定一个快照。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = RLock()
        self._snapshot: Snapshot | None = None

    @property
    def snapshot(self) -> Snapshot:
        if self._snapshot is None:
            raise RuntimeError("资料尚未加载")
        return self._snapshot

    def reload(self) -> Snapshot:
        with self._lock:
            candidate = load_catalog(
                self.settings.data_root, self.settings.schema_root, self.settings.enabled_phase
            )
            self._snapshot = candidate
            return candidate


class Batch(BaseModel):
    """内部批量只读请求；整批失败时不返回不完整结果。"""

    model_config = ConfigDict(extra="forbid")
    keys: list[str] = Field(min_length=1, max_length=100)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings.from_env()
    store = CatalogStore(config)

    @asynccontextmanager
    async def lifespan(_):
        config.validate()
        store.reload()
        yield

    app = FastAPI(title="问仙录 JSON 资料库", version="1", lifespan=lifespan)
    app.state.catalog = store

    def authorize(expected, scheme):
        bearer = HTTPBearer(auto_error=False, scheme_name=scheme)

        def check(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
            supplied = credentials.credentials if credentials else ""
            if not hmac.compare_digest(supplied.encode(), expected.encode()):
                raise HTTPException(401, "未授权", headers={"WWW-Authenticate": "Bearer"})

        return check

    service_auth = authorize(config.service_token, "ServiceToken")
    reload_auth = authorize(config.reload_token, "ReloadToken")

    def list_entries(public, kind, phase, keys, offset, limit):
        snapshot = store.snapshot
        if kind is not None and kind not in TYPES:
            raise HTTPException(422, "未知资料类别")
        if phase is not None and not 1 <= phase <= snapshot.enabled_phase:
            raise HTTPException(422, "内容阶段尚未启用")
        if keys:
            if any(not REFERENCE.fullmatch(key) for key in keys):
                raise HTTPException(422, "资料编号须为 类别:key")
            if any(key not in snapshot.records for key in keys):
                raise HTTPException(404, "请求的资料不存在或尚未启用")
        selected = snapshot.select(kind=kind, phase=phase, keys=keys or None)
        return {
            "count": len(selected),
            "offset": offset,
            "limit": limit,
            "revision": snapshot.revision,
            "content_hash": snapshot.revision.removeprefix("sha256:"),
            "results": [snapshot.entry(key, public=public) for key in selected[offset : offset + limit]],
        }

    def detail(kind, key, public):
        snapshot = store.snapshot
        reference = f"{kind}:{key}"
        if reference not in snapshot.records:
            raise HTTPException(404, "资料不存在或尚未启用")
        return snapshot.entry(reference, public=public)

    @app.get("/health")
    def health():
        snapshot = store.snapshot
        return {
            "status": "ready",
            "revision": snapshot.revision,
            "content_hash": snapshot.revision.removeprefix("sha256:"),
        }

    @app.get("/api/library/v1/public/entries")
    def public_entries(
        kind: str | None = None,
        phase: int | None = None,
        keys: list[str] | None = Query(None, max_length=100),
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
    ):
        return list_entries(True, kind, phase, keys, offset, limit)

    @app.get("/api/library/v1/public/{kind}/{key}")
    def public_detail(kind: str, key: str):
        return detail(kind, key, True)

    @app.get("/api/library/v1/internal/entries", dependencies=[Depends(service_auth)])
    def internal_entries(
        kind: str | None = None,
        phase: int | None = None,
        keys: list[str] | None = Query(None, max_length=100),
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
    ):
        return list_entries(False, kind, phase, keys, offset, limit)

    @app.post("/api/library/v1/internal/batch", dependencies=[Depends(service_auth)])
    def internal_batch(body: Batch):
        snapshot = store.snapshot
        if any(not REFERENCE.fullmatch(key) for key in body.keys):
            raise HTTPException(422, "资料编号须为 类别:key")
        if any(key not in snapshot.records for key in body.keys):
            raise HTTPException(404, "请求的资料不存在或尚未启用")
        return {
            "revision": snapshot.revision,
            "content_hash": snapshot.revision.removeprefix("sha256:"),
            "results": [snapshot.entry(key, public=False) for key in body.keys],
        }

    @app.get("/api/library/v1/internal/{kind}/{key}", dependencies=[Depends(service_auth)])
    def internal_detail(kind: str, key: str):
        return detail(kind, key, False)

    @app.post("/api/library/v1/ops/reload", dependencies=[Depends(reload_auth)])
    def reload_catalog():
        try:
            snapshot = store.reload()
        except CatalogError as error:
            raise HTTPException(
                409, {"code": "CATALOG_INVALID", "issues": [asdict(i) for i in error.issues]}
            ) from None
        return {
            "revision": snapshot.revision,
            "content_hash": snapshot.revision.removeprefix("sha256:"),
            "entries": len(snapshot.records),
        }

    return app
