"""Workbench 静态资源托管测试（spec 2026-10-25_add-workbench-mvp，F2/F3）。

task_0035 起 dist 根按 env → 仓库根 → 包内 → 旧 cwd 顺序解析（`--cwd` 只
决定任务目录）；本文件用 monkeypatch 把仓库根/包内候选钉到不存在路径，
隔离出旧 cwd 兜底与空态页的行为，真实仓库根候选由
`test_committed_dist_served_from_real_repo_root` 覆盖。
"""

from __future__ import annotations

import http.client
import os
import threading
import time
from pathlib import Path

import pytest

import nexuscli.runtime.api as api_module
from nexuscli.runtime.api import RuntimeApiServer


def _runtime_test_key() -> str:
    # 与 tests/test_runtime.py 的同名 helper 同语义。本文件不 `from tests.…`
    # 导入：任务 verify 用裸 `pytest`（非 `python -m pytest`）跑单文件时，
    # 仓库根不在 sys.path，`tests` 命名空间包不可导入（task_0035 verify 踩坑）。
    return os.environ.get("NEXUSCLI_UNITTEST_RUNTIME_CREDENTIAL") or "unit-test-value"


KEY = _runtime_test_key()


def _request(port: int, path: str) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path)
    response = conn.getresponse()
    data = response.read()
    headers = {k.lower(): v for k, v in response.getheaders()}
    conn.close()
    return response.status, headers, data


@pytest.fixture()
def workbench_server(tmp_path, monkeypatch):
    from nexuscli.config import load_config

    monkeypatch.delenv("NEXUSCLI_WORKBENCH_DIST", raising=False)
    server = RuntimeApiServer(
        cwd=str(tmp_path),
        config=load_config(project_root=None),
        api_key=KEY,
        port=0,
        workbench=True,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while server._server is None and time.monotonic() < deadline:
        time.sleep(0.02)
    port = int(server._server.server_address[1])  # type: ignore[index]
    time.sleep(0.1)
    yield server, port, tmp_path
    server._stop.set()
    thread.join(timeout=5)


def test_no_workbench_flag_serves_404(tmp_path) -> None:
    from nexuscli.config import load_config

    server = RuntimeApiServer(
        cwd=str(tmp_path), config=load_config(project_root=None), api_key=KEY, port=0
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while server._server is None and time.monotonic() < deadline:
        time.sleep(0.02)
    port = int(server._server.server_address[1])  # type: ignore[index]
    try:
        status, _, _ = _request(port, "/")  # 无鉴权头先撞 401 鉴权门
        assert status == 401
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/", headers={"x-api-key": KEY})
        response = conn.getresponse()
        assert response.status == 404  # 行为与今天一致：不带 --workbench 无静态路由
        conn.close()
    finally:
        server._stop.set()
        thread.join(timeout=5)


def _pin_repo_and_package_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """把仓库根/包内 dist 候选钉到不存在路径，隔离出旧 cwd 兜底（task_0035）。"""
    monkeypatch.setattr(api_module, "_repo_workbench_dist", lambda: tmp_path / "no-repo-dist")
    monkeypatch.setattr(
        api_module, "_packaged_workbench_dist", lambda: tmp_path / "no-package-dist"
    )


def test_empty_state_page_when_dist_missing(workbench_server, monkeypatch) -> None:
    _server, port, tmp = workbench_server
    _pin_repo_and_package_missing(tmp, monkeypatch)
    status, headers, body = _request(port, "/")
    assert status == 200
    assert headers["content-type"] == "text/html; charset=utf-8"
    text = body.decode("utf-8")
    assert "未构建" in text
    assert "npm" in text
    assert "/v1/*" in text
    # task_0035：空态页列出已尝试候选路径与修复指引，不再只说"未构建"
    assert "已尝试" in text
    assert "no-repo-dist" in text
    assert "no-package-dist" in text
    assert "NEXUSCLI_WORKBENCH_DIST" in text


def test_serves_dist_files_with_content_types(workbench_server, monkeypatch) -> None:
    _server, port, tmp = workbench_server
    _pin_repo_and_package_missing(tmp, monkeypatch)
    dist = tmp / "frontend" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><html><body>workbench</body></html>")
    (dist / "assets" / "app.js").write_text("console.log(1);")
    (dist / "assets" / "app.css").write_text("body{}")
    (dist / "favicon.svg").write_text("<svg/>")

    status, headers, body = _request(port, "/")
    assert status == 200
    assert headers["content-type"] == "text/html; charset=utf-8"
    assert b"workbench" in body

    status, headers, body = _request(port, "/assets/app.js")
    assert status == 200
    assert headers["content-type"] == "text/javascript; charset=utf-8"

    status, headers, _ = _request(port, "/assets/app.css")
    assert headers["content-type"] == "text/css; charset=utf-8"

    status, headers, _ = _request(port, "/favicon.svg")
    assert headers["content-type"] == "image/svg+xml"


def test_path_traversal_rejected(workbench_server) -> None:
    _server, port, _tmp = workbench_server
    for path in (
        "/../etc/passwd",
        "/..%2Fetc%2Fpasswd",
        "/..\\passwd",
        "/a/../../passwd",
        "/%2e%2e/passwd",
    ):
        status, _, _ = _request(port, path)
        assert status == 404, path


def test_spa_fallback_and_404(workbench_server, monkeypatch) -> None:
    _server, port, tmp = workbench_server
    _pin_repo_and_package_missing(tmp, monkeypatch)
    dist = tmp / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html>")

    status, _, body = _request(port, "/threads/some-id")  # 无后缀 → SPA 回退
    assert status == 200 and b"doctype" in body.lower()
    status, _, _ = _request(port, "/missing.js")  # 带后缀缺失 → 404
    assert status == 404
    status, _, _ = _request(port, "/v1/threads")  # /v1/* 永不落入静态分支
    assert status == 401  # 无鉴权头 → 走 API 鉴权门，不是静态


def test_legacy_cwd_dist_still_served(workbench_server, monkeypatch) -> None:
    """task_0035 后旧 `cwd/frontend/dist` 降为最后兜底：仓库根/包内落空时仍托管。"""

    _server, port, tmp = workbench_server
    _pin_repo_and_package_missing(tmp, monkeypatch)
    # 本用例在 tmp cwd 下验证「旧 cwd 布局 dist 在位即托管」；真实仓库 dist 的
    # 入库断言由 frontend 构建 verify（npm run build + dist in sync）覆盖。
    dist = tmp / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>committed</title>")
    status, _, body = _request(port, "/")
    assert status == 200 and b"committed" in body


def test_committed_dist_served_from_real_repo_root(tmp_path) -> None:
    """F3 提交 dist 后：以仓库根为 cwd 的 server 托管真实构建产物。

    dist 缺失（wheel 检出形态）时 pytest.skip——入库断言由 npm build verify 承担。
    """

    from nexuscli.config import load_config

    repo_root = Path(__file__).resolve().parents[1]
    dist = repo_root / "frontend" / "dist"
    if not (dist / "index.html").is_file():
        pytest.skip("frontend/dist 未构建（wheel 检出形态）")

    server = RuntimeApiServer(
        cwd=str(repo_root),
        config=load_config(project_root=None),
        api_key=KEY,
        port=0,
        workbench=True,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while server._server is None and time.monotonic() < deadline:
        time.sleep(0.02)
    port = int(server._server.server_address[1])  # type: ignore[index]
    try:
        status, headers, body = _request(port, "/")
        assert status == 200
        # 大小写不敏感比较：Vite react-ts 模板产出小写 doctype（02 勘误口径）
        assert body[:15].decode("utf-8").lower() == "<!doctype html>"
        assert headers["content-type"] == "text/html; charset=utf-8"
        assets_status, _, _ = _request(port, "/" + _first_asset(dist))
        assert assets_status == 200
    finally:
        server._stop.set()
        thread.join(timeout=5)


def _first_asset(dist: Path) -> str:
    assets = dist / "assets"
    for entry in sorted(assets.iterdir()):
        if entry.is_file():
            return f"assets/{entry.name}"
    return "index.html"
