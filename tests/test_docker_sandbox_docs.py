"""Doc contract: the docker sandbox backend's claims must stay runnable-honest.

permissions.md and README describe the backend's semantics and its honest
boundaries; these assertions pin the load-bearing phrases so later edits
cannot silently drop them (and the old whole-sandbox debt wording cannot
come back).
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_PERMISSIONS = _ROOT / "docs" / "permissions.md"
_README = _ROOT / "README.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_permissions_documents_docker_backend_row() -> None:
    text = _read(_PERMISSIONS)
    assert "`docker`" in text, "permissions.md 缺 docker 后端表格行"
    for token in (
        "--read-only",
        "--network=none",
        "--tmpfs /tmp",
        "sandbox.docker_image",
        "python:3.11-slim",
    ):
        assert token in text, f"permissions.md docker 行缺 {token}"


def test_permissions_documents_network_and_tmpfs_boundaries() -> None:
    text = _read(_PERMISSIONS)
    assert "整体放行" in text and "粒度不对称" in text, (
        "permissions.md 缺 allow_network 全有全无与 seatbelt 粒度不对称的如实边界"
    )
    assert "不与宿主临时目录共享" in text, "permissions.md 缺容器 /tmp 独立 tmpfs 边界"


def test_permissions_fail_closed_mentions_docker() -> None:
    text = _read(_PERMISSIONS)
    assert "daemon 未运行时请求 docker" in text, (
        "permissions.md fail-closed 语义未覆盖 docker CLI/daemon 缺失"
    )


def test_readme_boundary_sentence_updated() -> None:
    text = _read(_README)
    assert "OS 级沙箱仍挂账" not in text, "README 仍保留整句『OS 级沙箱仍挂账』的旧口径"
    assert "docker" in text and "Windows" in text and "挂账" in text, (
        "README 应说明四后端可用、仅 Windows 后端挂账"
    )
