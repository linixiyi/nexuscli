"""Workbench 静态产物的发布形态测试（spec 2026-11-07_fix-nexuscli-release-hardening，task_0004）。

覆盖 `_workbench_dist_root()` 的解析顺序（task_0035 现行契约）：`NEXUSCLI_WORKBENCH_DIST`
最高优先 → 仓库根 `frontend/dist` → 包内 `nexuscli/frontend/dist`（wheel
force-include 产物）→ 旧 cwd `frontend/dist`（最后兜底），每级都要求 index.html
在位。源码检出形态下仓库根候选真实存在（frontend/dist 是 e2e 构建产物）、包内
dist 不存在，故回落类用例用 monkeypatch `_repo_workbench_dist` / 隔离仓库根候选、
`_packaged_workbench_dist` 模拟安装布局来验证。
"""

from __future__ import annotations

import http.client
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import nexuscli.runtime.api as api_module
from nexuscli.runtime.api import RuntimeApiServer


def _runtime_test_key() -> str:
    # 与 tests/test_runtime.py 的同名 helper 同语义。本文件不 `from tests.…`
    # 导入：任务 verify 用裸 `pytest`（非 `python -m pytest`）跑单文件时，
    # 仓库根不在 sys.path，`tests` 命名空间包不可导入。
    return os.environ.get("NEXUSCLI_UNITTEST_RUNTIME_CREDENTIAL") or "unit-test-value"


KEY = _runtime_test_key()


def _request(port: int, path: str) -> tuple[int, dict[str, str], bytes]:
    # 静态资源在鉴权门之前分流（api.py `_handle`），无需凭证头。
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path)
    response = conn.getresponse()
    data = response.read()
    headers = {k.lower(): v for k, v in response.getheaders()}
    conn.close()
    return response.status, headers, data


def _make_server(cwd: Path) -> RuntimeApiServer:
    from nexuscli.config import load_config

    return RuntimeApiServer(
        cwd=str(cwd),
        config=load_config(project_root=None),
        api_key=KEY,
        port=0,
        workbench=True,
    )


@contextmanager
def _serving(server: RuntimeApiServer) -> Iterator[int]:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while server._server is None and time.monotonic() < deadline:
        time.sleep(0.02)
    port = int(server._server.server_address[1])  # type: ignore[index]
    time.sleep(0.1)
    try:
        yield port
    finally:
        server._stop.set()
        thread.join(timeout=5)


@pytest.fixture()
def no_workbench_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEXUSCLI_WORKBENCH_DIST", raising=False)


def _fake_packaged_dist(root: Path, title: str) -> Path:
    dist = root / "packaged" / "nexuscli" / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text(f"<!doctype html><title>{title}</title>")
    return dist


def _isolate_repo_dist(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """隔离 task_0035 的仓库根候选（``<repo>/frontend/dist``）。

    源码检出形态下该候选真实存在（frontend/dist 是 e2e 的构建产物），而回落
    类用例的前提是「repo 之外无 dist」——不隔离它，包内/cwd 回落断言永远
    轮不到。仅影响显式调用的用例，不隐藏 repo 候选本身的语义。
    """
    monkeypatch.setattr(api_module, "_repo_workbench_dist", lambda: tmp_path / "no-repo-dist")


def test_dist_root_falls_back_to_package_when_cwd_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_workbench_env: None
) -> None:
    """repo 候选与 cwd 都没有 frontend/dist 时回落到包内路径。"""

    _isolate_repo_dist(monkeypatch, tmp_path)
    packaged = _fake_packaged_dist(tmp_path, "packaged")
    monkeypatch.setattr(api_module, "_packaged_workbench_dist", lambda: packaged)
    server = _make_server(tmp_path)
    assert server._workbench_dist_root() == packaged
    # 回落必须落在包内候选上，而不是旧 cwd 兜底（cwd 下根本没有 dist）。
    assert server._workbench_dist_root() != tmp_path / "frontend" / "dist"


def test_cwd_dist_without_index_falls_back_to_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_workbench_env: None
) -> None:
    """cwd 有 dist 目录但缺 index.html（半截构建）时同样回落包内。"""

    _isolate_repo_dist(monkeypatch, tmp_path)
    (tmp_path / "frontend" / "dist").mkdir(parents=True)
    packaged = _fake_packaged_dist(tmp_path, "packaged")
    monkeypatch.setattr(api_module, "_packaged_workbench_dist", lambda: packaged)
    server = _make_server(tmp_path)
    assert server._workbench_dist_root() == packaged


def test_env_override_beats_cwd_and_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`NEXUSCLI_WORKBENCH_DIST` 仍是最高优先级，cwd dist 在位也不夺权。"""

    env_dist = tmp_path / "env-dist"
    env_dist.mkdir()
    (env_dist / "index.html").write_text("<!doctype html><title>env</title>")
    monkeypatch.setenv("NEXUSCLI_WORKBENCH_DIST", str(env_dist))
    server = _make_server(tmp_path)
    assert server._workbench_dist_root() == env_dist


def test_serves_packaged_dist_when_cwd_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_workbench_env: None
) -> None:
    """wheel 安装形态：repo 候选与 cwd 无 dist 时 GET / 托管包内产物而不是空态页。"""

    _isolate_repo_dist(monkeypatch, tmp_path)
    packaged = _fake_packaged_dist(tmp_path, "packaged")
    monkeypatch.setattr(api_module, "_packaged_workbench_dist", lambda: packaged)
    project = tmp_path / "user-project"
    project.mkdir()
    with _serving(_make_server(project)) as port:
        status, headers, body = _request(port, "/")
    assert status == 200
    assert headers["content-type"] == "text/html; charset=utf-8"
    assert b"packaged" in body


def test_packaged_dist_beats_legacy_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_workbench_env: None
) -> None:
    """task_0035 候选序：包内产物在位时优先于旧 cwd/frontend/dist 兜底。

    旧契约（cwd dist 夺权）在 task_0035 把 dist 根锚定安装形态后反转：
    cwd/frontend/dist 降级为最后兜底，包内候选在位必先命中。
    """

    _isolate_repo_dist(monkeypatch, tmp_path)
    cwd_dist = tmp_path / "frontend" / "dist"
    cwd_dist.mkdir(parents=True)
    (cwd_dist / "index.html").write_text("<!doctype html><title>cwd</title>")
    monkeypatch.setattr(
        api_module, "_packaged_workbench_dist", lambda: _fake_packaged_dist(tmp_path, "packaged")
    )
    with _serving(_make_server(tmp_path)) as port:
        status, _, body = _request(port, "/")
    assert status == 200
    assert b"packaged" in body
    assert b"cwd" not in body


def test_empty_state_still_shown_when_no_dist_anywhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_workbench_env: None
) -> None:
    """repo 候选、包内与 cwd 都没有 dist → 仍回空态页（不 500、不裸 404）。"""

    _isolate_repo_dist(monkeypatch, tmp_path)
    missing = tmp_path / "not-a-dist"
    monkeypatch.setattr(api_module, "_packaged_workbench_dist", lambda: missing)
    with _serving(_make_server(tmp_path)) as port:
        status, headers, body = _request(port, "/")
    assert status == 200
    assert headers["content-type"] == "text/html; charset=utf-8"
    text = body.decode("utf-8")
    assert "未构建" in text
    assert "npm" in text
