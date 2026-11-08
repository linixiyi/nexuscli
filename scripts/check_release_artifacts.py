#!/usr/bin/env python3
"""校验发布产物内容：wheel 自带 workbench，sdist 不泄漏依赖目录与内部归档。

spec 2026-11-07_fix-nexuscli-release-hardening task_0004。用 `uv build` 在临时
目录构建 wheel + sdist（不污染仓库根 dist/），然后断言：

- wheel 含 `nexuscli/frontend/dist/index.html`（workbench 随包发行，
  对应 pyproject 的 `[tool.hatch.build.targets.wheel.force-include]`）；
- sdist 按路径段不含 `node_modules` / `.spec`（任务指定），外加 sdist
  排除清单里的 `frontend/test-results`（同一份排除配置的第三个目标，
  泄漏同样应红）。

任一断言失败时打印缺失/多余路径清单并 exit 1；全部通过打印产物名后 exit 0。
"""

from __future__ import annotations

import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# wheel 必须携带的工作台入口（force-include 的映射目标）。
_WHEEL_REQUIRED = "nexuscli/frontend/dist/index.html"

# sdist 里按路径段判定的禁止项：任一路径段命中即泄漏。
_SDIST_FORBIDDEN_SEGMENTS = ("node_modules", ".spec", "test-results")

# 泄漏往往成千上万条（node_modules 一次 3800+），示例只展示头部。
_LEAK_SAMPLE_SIZE = 20


def _run_uv_build(out_dir: Path) -> None:
    proc = subprocess.run(
        ["uv", "build", "--out-dir", str(out_dir)],
        cwd=_ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit("ERROR: uv build 失败，无法校验发布产物")


def _find_artifact(out_dir: Path, pattern: str) -> Path:
    matches = sorted(out_dir.glob(pattern))
    if len(matches) != 1:
        raise SystemExit(f"ERROR: 期望恰好 1 个 {pattern}，实际找到 {len(matches)} 个")
    return matches[0]


def _wheel_problems(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
    if _WHEEL_REQUIRED in names:
        return []
    packaged_frontend = sorted(name for name in names if name.startswith("nexuscli/frontend/"))
    hint = f"（包内 frontend 相关条目：{packaged_frontend or '无'}）"
    return [f"wheel 缺少 {_WHEEL_REQUIRED} {hint}"]


def _sdist_problems(sdist: Path) -> list[str]:
    with tarfile.open(sdist, "r:gz") as tf:
        names = tf.getnames()
    problems: list[str] = []
    for segment in _SDIST_FORBIDDEN_SEGMENTS:
        leaked = sorted(name for name in names if segment in Path(name).parts)
        if not leaked:
            continue
        sample = leaked[:_LEAK_SAMPLE_SIZE]
        more = "" if len(leaked) <= len(sample) else f" …（共 {len(leaked)} 条）"
        problems.append(f"sdist 泄漏 `{segment}/`：{sample}{more}")
    return problems


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="nexuscli-release-artifacts-") as tmp:
        out_dir = Path(tmp)
        _run_uv_build(out_dir)
        wheel = _find_artifact(out_dir, "*.whl")
        sdist = _find_artifact(out_dir, "*.tar.gz")
        problems = _wheel_problems(wheel) + _sdist_problems(sdist)
    if problems:
        print("check_release_artifacts: 发布产物校验失败")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"check_release_artifacts: OK wheel={wheel.name} sdist={sdist.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
