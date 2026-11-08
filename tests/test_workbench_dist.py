"""Workbench dist 根四级解析测试（spec 2026-11-07_fix-nexuscli-release-hardening，task_0035）。

`_workbench_dist_root()` 按 env `NEXUSCLI_WORKBENCH_DIST` → 仓库根
`frontend/dist` → 包内 `nexuscli/frontend/dist` → 旧 `cwd/frontend/dist`
顺序解析，每步要求 index.html 在位，缺失即落到下一级。`--cwd` 只决定
任务目录，不再决定 dist 根（旧 cwd 布局仅作最后兜底）。全落空时空态页
列出已尝试路径与修复指引。

仓库根/包内候选通过 monkeypatch 模块级 `_repo_workbench_dist` /
`_packaged_workbench_dist` 钉到临时路径，保证解析顺序不依赖运行环境；
真实仓库根候选单独用 skip 守卫覆盖。
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
from nexuscli.runtime.api import RuntimeApiServer, _repo_workbench_dist

KEY = os.environ.get("NEXUSCLI_UNITTEST_RUNTIME_CREDENTIAL") or "unit-test-value"


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


def _dist_with_index(root: Path, title: str) -> Path:
    dist = root / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text(f"<!doctype html><title>{title}</title>")
    return dist


def _dist_without_index(root: Path) -> Path:
    dist = root / "dist"
    dist.mkdir(parents=True)
    return dist


def _pin_candidates(
    monkeypatch: pytest.MonkeyPatch,
    repo: Path | None,
    packaged: Path | None,
) -> None:
    """钉住仓库根/包内候选（None = 该路径不存在），隔离运行环境差异。"""
    monkeypatch.setattr(
        api_module,
        "_repo_workbench_dist",
        lambda: repo if repo is not None else Path("/nonexistent/repo-dist"),
    )
    monkeypatch.setattr(
        api_module,
        "_packaged_workbench_dist",
        lambda: packaged if packaged is not None else Path("/nonexistent/package-dist"),
    )


@pytest.fixture()
def no_workbench_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEXUSCLI_WORKBENCH_DIST", raising=False)


def test_repo_helper_points_at_checkout_root() -> None:
    """`_repo_workbench_dist` 独立复算：api.py 在 <repo>/src/nexuscli/runtime/ 下。"""

    expected = Path(api_module.__file__).resolve().parents[3] / "frontend" / "dist"
    assert _repo_workbench_dist() == expected


def test_env_override_wins_over_repo_package_and_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """env 指定值（含 index.html）最高优先，仓库根/包内/cwd dist 在位也不夺权。"""

    env_dist = _dist_with_index(tmp_path / "env", "env")
    monkeypatch.setenv("NEXUSCLI_WORKBENCH_DIST", str(env_dist))
    _pin_candidates(
        monkeypatch,
        repo=_dist_with_index(tmp_path / "repo", "repo"),
        packaged=_dist_with_index(tmp_path / "package", "package"),
    )
    cwd_dist = tmp_path / "frontend" / "dist"
    cwd_dist.mkdir(parents=True)
    (cwd_dist / "index.html").write_text("<!doctype html><title>cwd</title>")
    server = _make_server(tmp_path)
    assert server._workbench_dist_root() == env_dist


def test_env_without_index_falls_through_to_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """env 指向缺 index.html 的目录（每步都检查）→ 落到下一级仓库根候选。"""

    monkeypatch.setenv("NEXUSCLI_WORKBENCH_DIST", str(_dist_without_index(tmp_path / "env")))
    repo_dist = _dist_with_index(tmp_path / "repo", "repo")
    _pin_candidates(monkeypatch, repo=repo_dist, packaged=None)
    server = _make_server(tmp_path)
    assert server._workbench_dist_root() == repo_dist


def test_repo_root_beats_package_and_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_workbench_env: None
) -> None:
    """仓库根候选优先于包内与旧 cwd：源码检出形态不再依赖 --cwd。"""

    repo_dist = _dist_with_index(tmp_path / "repo", "repo")
    _pin_candidates(
        monkeypatch, repo=repo_dist, packaged=_dist_with_index(tmp_path / "package", "package")
    )
    cwd_dist = tmp_path / "frontend" / "dist"
    cwd_dist.mkdir(parents=True)
    (cwd_dist / "index.html").write_text("<!doctype html><title>cwd</title>")
    server = _make_server(tmp_path)
    assert server._workbench_dist_root() == repo_dist


def test_repo_without_index_falls_to_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_workbench_env: None
) -> None:
    """仓库根有 dist 目录但缺 index.html（半截构建）→ 落到包内候选。"""

    _pin_candidates(
        monkeypatch,
        repo=_dist_without_index(tmp_path / "repo"),
        packaged=_dist_with_index(tmp_path / "package", "package"),
    )
    server = _make_server(tmp_path)
    assert server._workbench_dist_root() == tmp_path / "package" / "dist"


def test_packaged_beats_cwd_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_workbench_env: None
) -> None:
    """包内候选优先于旧 cwd 布局（cwd 降为最后兜底）。"""

    _pin_candidates(
        monkeypatch, repo=None, packaged=_dist_with_index(tmp_path / "package", "package")
    )
    cwd_dist = tmp_path / "frontend" / "dist"
    cwd_dist.mkdir(parents=True)
    (cwd_dist / "index.html").write_text("<!doctype html><title>cwd</title>")
    server = _make_server(tmp_path)
    assert server._workbench_dist_root() == tmp_path / "package" / "dist"


def test_packaged_without_index_falls_to_cwd_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_workbench_env: None
) -> None:
    """包内候选缺 index.html → 落到旧 cwd/frontend/dist 兜底。"""

    _pin_candidates(monkeypatch, repo=None, packaged=_dist_without_index(tmp_path / "package"))
    cwd_dist = tmp_path / "frontend" / "dist"
    cwd_dist.mkdir(parents=True)
    (cwd_dist / "index.html").write_text("<!doctype html><title>cwd</title>")
    server = _make_server(tmp_path)
    assert server._workbench_dist_root() == cwd_dist


def test_cwd_legacy_is_last_resort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_workbench_env: None
) -> None:
    """仓库根/包内都落空、旧 cwd 布局 dist 在位 → 用 cwd 兜底（HTTP 托管）。"""

    _pin_candidates(monkeypatch, repo=None, packaged=None)
    cwd_dist = tmp_path / "frontend" / "dist"
    cwd_dist.mkdir(parents=True)
    (cwd_dist / "index.html").write_text("<!doctype html><title>legacy-cwd</title>")
    with _serving(_make_server(tmp_path)) as port:
        status, headers, body = _request(port, "/")
    assert status == 200
    assert headers["content-type"] == "text/html; charset=utf-8"
    assert b"legacy-cwd" in body


def test_cwd_only_decides_task_dir_not_dist_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_workbench_env: None
) -> None:
    """--cwd 只决定任务目录：cwd 自带 dist 也不再夺仓库根候选的权。"""

    repo_dist = _dist_with_index(tmp_path / "repo", "repo")
    _pin_candidates(monkeypatch, repo=repo_dist, packaged=None)
    project = tmp_path / "user-project"
    project_dist = project / "frontend" / "dist"
    project_dist.mkdir(parents=True)
    (project_dist / "index.html").write_text("<!doctype html><title>project</title>")
    server = _make_server(project)
    assert server._workbench_dist_root() == repo_dist
    with _serving(server) as port:
        status, _, body = _request(port, "/")
    assert status == 200
    assert b"repo" in body
    assert b"project" not in body


def test_all_candidates_miss_serves_empty_state_with_tried_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_workbench_env: None
) -> None:
    """全落空（含候选缺 index.html）：空态页列出已尝试路径与修复指引。"""

    _pin_candidates(monkeypatch, repo=_dist_without_index(tmp_path / "repo"), packaged=None)
    with _serving(_make_server(tmp_path)) as port:
        status, headers, body = _request(port, "/")
    assert status == 200
    assert headers["content-type"] == "text/html; charset=utf-8"
    text = body.decode("utf-8")
    assert "未构建" in text
    # 已尝试候选路径逐条列出：缺 index.html 的仓库根候选、缺失的包内候选、旧 cwd
    assert str(tmp_path / "repo" / "dist") in text
    assert "/nonexistent/package-dist" in text
    assert str(tmp_path / "frontend" / "dist") in text
    # 修复指引：env 指定与源码构建两条路
    assert "NEXUSCLI_WORKBENCH_DIST" in text
    assert "npm run build" in text
    assert "/v1/*" in text


def test_all_candidates_miss_root_falls_back_to_last_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_workbench_env: None
) -> None:
    """全落空时 `_workbench_dist_root()` 返回最后候选（旧 cwd 回落），供空态页流程使用。"""

    _pin_candidates(monkeypatch, repo=None, packaged=None)
    server = _make_server(tmp_path)
    assert server._workbench_dist_root() == tmp_path / "frontend" / "dist"


def test_real_source_checkout_serves_repo_dist(tmp_path: Path, no_workbench_env: None) -> None:
    """真实源码检出形态：--cwd 指向用户项目时仍托管仓库根 dist（缺失则 skip）。"""

    repo_dist = _repo_workbench_dist()
    if not (repo_dist / "index.html").is_file():
        pytest.skip("仓库根 frontend/dist 未构建（wheel 检出形态）")

    project = tmp_path / "user-project"
    project.mkdir()
    with _serving(_make_server(project)) as port:
        status, headers, body = _request(port, "/")
    assert status == 200
    assert headers["content-type"] == "text/html; charset=utf-8"
    # 大小写不敏感比较：Vite react-ts 模板产出小写 doctype（02 勘误口径）
    assert body[:15].decode("utf-8").lower() == "<!doctype html>"
