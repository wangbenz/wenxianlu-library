"""使用临时资料与自建 loopback 进程验证真实 HTTP；不接数据库，不使用真实令牌。"""

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    with tempfile.TemporaryDirectory(prefix="wenxian-library-http-") as directory:
        scratch = Path(directory)
        data = scratch / "data"
        shutil.copytree(ROOT / "data", data)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        service_token, reload_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        env = {
            **os.environ,
            "LIBRARY_DATA_ROOT": str(data),
            "LIBRARY_SCHEMA_ROOT": str(ROOT / "schema"),
            "LIBRARY_ENABLED_PHASE": "1",
            "LIBRARY_SERVICE_TOKEN": service_token,
            "LIBRARY_RELOAD_TOKEN": reload_token,
        }
        log = (scratch / "server.log").open("w+")
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "library_service.api:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--workers",
                "1",
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=log,
        )

        def request(path, method="GET", token=None):
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as error:
                return error.code, json.load(error)

        try:
            ready = False
            for _ in range(100):
                if process.poll() is not None:
                    raise AssertionError("临时服务启动失败")
                try:
                    ready = request("/health")[0] == 200
                except OSError:
                    pass
                if ready:
                    break
                time.sleep(0.1)
            assert ready, "临时服务未就绪"
            status, before = request("/api/library/v1/public/place/qingxi-town")
            assert status == 200
            assert set(before) == {"type", "key", "name", "phase", "public", "revision", "content_hash"}
            assert request("/api/library/v1/internal/entries")[0] == 401
            assert request("/api/library/v1/internal/place/qingxi-town", token=service_token)[1]["secret"][
                "notes"
            ]
            assert request("/api/library/v1/ops/reload", "POST", service_token)[0] == 401
            file = data / "items/spirit_stone/spirit-stone.json"
            original = file.read_bytes()
            broken = json.loads(original)
            broken["rules"]["price"].update(min=100, max=1)
            file.write_text(json.dumps(broken, ensure_ascii=False))
            assert request("/api/library/v1/ops/reload", "POST", reload_token)[0] == 409
            assert request("/api/library/v1/public/place/qingxi-town")[1] == before
            file.write_bytes(original)
            place = data / "places/qingxi-town.json"
            changed = json.loads(place.read_text())
            changed["public"]["description"] = "HTTP 检查的新快照"
            place.write_text(json.dumps(changed, ensure_ascii=False))
            assert request("/api/library/v1/ops/reload", "POST", reload_token)[0] == 200
            assert (
                request("/api/library/v1/public/place/qingxi-town")[1]["public"]["description"]
                == "HTTP 检查的新快照"
            )
            assert request("/openapi.json")[0] == 200
            print(
                json.dumps(
                    {
                        "http_smoke": "passed",
                        "public_projection": True,
                        "authorization": True,
                        "failed_reload_preserves_old_snapshot": True,
                        "successful_reload": True,
                    }
                )
            )
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            log.close()


if __name__ == "__main__":
    main()
