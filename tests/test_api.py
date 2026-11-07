"""公开投影、认证边界、失败恢复和并发快照验证。"""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest
from fastapi.testclient import TestClient

from library_service import api
from library_service.api import create_app
from library_service.catalog import CatalogError
from tests.test_catalog import edit


def headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_public_projection_has_only_allowed_fields(complete, config):
    with TestClient(create_app(config)) as client:
        response = client.get("/api/library/v1/public/character/guide")
        assert response.status_code == 200
        assert set(response.json()) == {"type", "key", "name", "phase", "public", "revision", "content_hash"}
        assert "不可泄露" not in response.text
        assert "沉静" not in response.text
        rows = client.get("/api/library/v1/public/entries", params={"kind": "event"}).json()
        assert rows["results"][0]["public"] == {}
        assert "truth" not in json.dumps(rows)


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/library/v1/internal/entries"),
        ("GET", "/api/library/v1/internal/place/qingxi-town"),
        ("POST", "/api/library/v1/internal/batch"),
        ("POST", "/api/library/v1/ops/reload"),
    ],
)
def test_unauthenticated_internal_and_ops_rejected(config, method, path):
    with TestClient(create_app(config)) as client:
        for authorization in [{}, headers("wrong-token"), {"Authorization": "Basic invalid"}]:
            response = client.request(method, path, headers=authorization)
            assert response.status_code == 401
            assert config.service_token not in response.text and config.reload_token not in response.text


def test_role_tokens_are_not_interchangeable(config):
    with TestClient(create_app(config)) as client:
        assert (
            client.get("/api/library/v1/internal/entries", headers=headers(config.reload_token)).status_code
            == 401
        )
        assert (
            client.post("/api/library/v1/ops/reload", headers=headers(config.service_token)).status_code
            == 401
        )
        response = client.get(
            "/api/library/v1/internal/place/qingxi-town", headers=headers(config.service_token)
        )
        assert response.status_code == 200
        assert response.json()["secret"]["notes"]


def test_batch_and_query_filters(complete, config):
    with TestClient(create_app(config)) as client:
        response = client.post(
            "/api/library/v1/internal/batch",
            headers=headers(config.service_token),
            json={"keys": ["place:qingxi-town", "item:spirit-stone"]},
        )
        assert response.status_code == 200
        assert [r["key"] for r in response.json()["results"]] == ["qingxi-town", "spirit-stone"]
        response = client.get(
            "/api/library/v1/public/entries",
            params=[("keys", "place:qingxi-town"), ("keys", "item:spirit-stone")],
        )
        assert response.json()["count"] == 2
        response = client.get(
            "/api/library/v1/public/entries", params={"kind": "root", "phase": 1, "offset": 2, "limit": 2}
        )
        assert response.json()["count"] == 6 and len(response.json()["results"]) == 2
        for payload in [
            {"keys": []},
            {"keys": ["not-a-reference"]},
            {"keys": ["place:missing"]},
            {"keys": ["place:qingxi-town"], "extra": True},
        ]:
            assert client.post(
                "/api/library/v1/internal/batch", headers=headers(config.service_token), json=payload
            ).status_code in {404, 422}
        for query in [{"kind": "unknown"}, {"phase": 2}, {"limit": 0}, {"offset": -1}, {"limit": 101}]:
            assert client.get("/api/library/v1/public/entries", params=query).status_code == 422


def test_no_legacy_or_mutation_endpoints(config):
    with TestClient(create_app(config)) as client:
        assert client.get("/admin/").status_code == 404
        assert client.get("/api/library/v1/items/").status_code == 404
        for method in ["POST", "PUT", "PATCH", "DELETE"]:
            assert client.request(method, "/api/library/v1/public/place/qingxi-town").status_code == 405


def test_reload_failure_preserves_old_snapshot(config):
    with TestClient(create_app(config)) as client:
        before = client.get("/api/library/v1/public/place/qingxi-town").json()
        edit(config.data_root, "places/qingxi-town.json", lambda r: r["secret"].update(bad="DO-NOT-LEAK"))
        failed = client.post("/api/library/v1/ops/reload", headers=headers(config.reload_token))
        assert failed.status_code == 409
        assert failed.json()["detail"]["issues"][0]["field"] == "/secret"
        assert "DO-NOT-LEAK" not in failed.text
        assert client.get("/api/library/v1/public/place/qingxi-town").json() == before
        edit(config.data_root, "places/qingxi-town.json", lambda r: r["secret"].pop("bad"))
        edit(
            config.data_root, "places/qingxi-town.json", lambda r: r["public"].update(description="新的说明")
        )
        success = client.post("/api/library/v1/ops/reload", headers=headers(config.reload_token))
        assert success.status_code == 200
        assert (
            client.get("/api/library/v1/public/place/qingxi-town").json()["public"]["description"]
            == "新的说明"
        )


def test_reload_is_atomic_for_concurrent_readers(config, monkeypatch):
    with TestClient(create_app(config)) as client:
        before = client.get("/api/library/v1/public/place/qingxi-town").json()
        edit(config.data_root, "places/qingxi-town.json", lambda r: r["public"].update(description="新快照"))
        started, release = Event(), Event()
        original = api.load_catalog

        def slow(*args):
            started.set()
            assert release.wait(5)
            return original(*args)

        monkeypatch.setattr(api, "load_catalog", slow)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                client.post, "/api/library/v1/ops/reload", headers=headers(config.reload_token)
            )
            assert started.wait(5)
            assert client.get("/api/library/v1/public/place/qingxi-town").json() == before
            release.set()
            assert future.result(timeout=5).status_code == 200
        after = client.get("/api/library/v1/public/place/qingxi-town").json()
        assert after["revision"] != before["revision"] and after["public"]["description"] == "新快照"


@pytest.mark.parametrize("tokens", [("", ""), ("short", "other"), ("x" * 32, "x" * 32)])
def test_unsafe_auth_configuration_fails_startup(config, tokens):
    with pytest.raises(ValueError):
        with TestClient(create_app(replace(config, service_token=tokens[0], reload_token=tokens[1]))):
            pass


def test_bad_catalog_fails_before_serving(config):
    (config.data_root / "manifest.json").write_text("{}")
    with pytest.raises(CatalogError):
        with TestClient(create_app(config)):
            pass


def test_openapi_has_no_sample_secrets(config):
    app = create_app(config)
    schema = app.openapi()
    assert set(schema["components"]["securitySchemes"]) == {"ServiceToken", "ReloadToken"}
    assert schema["paths"]["/api/library/v1/internal/entries"]["get"]["security"] == [{"ServiceToken": []}]
    assert schema["paths"]["/api/library/v1/ops/reload"]["post"]["security"] == [{"ReloadToken": []}]
    assert "security" not in schema["paths"]["/api/library/v1/public/entries"]["get"]
    encoded = json.dumps(schema)
    assert config.service_token not in encoded and config.reload_token not in encoded


def test_no_database_access_and_no_token_in_config_repr(config, monkeypatch):
    import sqlite3

    def forbidden(*args, **kwargs):
        raise AssertionError("JSON 服务不得访问数据库")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    assert config.service_token not in repr(config) and config.reload_token not in repr(config)
    with TestClient(create_app(config)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/library/v1/public/entries").status_code == 200
        assert (
            client.post("/api/library/v1/ops/reload", headers=headers(config.reload_token)).status_code == 200
        )
