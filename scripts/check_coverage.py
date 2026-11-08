#!/usr/bin/env python3
"""覆盖率棘轮：pytest --cov 跑全量套件，只锁「不低于 baseline」，变低即 exit 1。

设计（task_0022）：
- 棘轮不是目标：baseline 由 `--write-baseline` 按当前实测值生成；之后任何一次
  运行低于该值就红，涨了不奖励。不追求 100%，也不设人工百分比目标。
- 内层 pytest 钉在 Python 3.11，独立 env 建在 .nexuscli/cov-venv（已 gitignore，
  不动仓库 .venv）：CI 门禁也只在 ubuntu 3.11 跑，覆盖率必须与测量环境同 Python
  版本才可比；同时绕开 3.13 全量套件 + coverage ctrace 的 livelock（实测 3.13 在
  tests/test_print_mode_commands.py 首条用例处挂死不前进，3.11 约 7 分钟跑完）。
- 脚本自身不 import pytest-cov（只读 JSON 报告）；--frozen 与 CI 的
  `uv sync --frozen` 同口径，锁不新鲜时快速失败而不是悄悄重解析。
- 覆盖率 JSON 与原始数据都落在 .nexuscli/，不在仓库根留残渣。
- baseline 记录 Python 版本、生成平台与生成命令：不同 Python/平台跑出的数字
  不可比，换环境后应重新 `--write-baseline`。比对时平台不一致只警告不失败
  （本机生成的基线不该让 ubuntu CI 首跑误红），percent 低于 baseline 才 exit 1。
  CI 只在 ubuntu 3.11 这一个矩阵项跑（全量
  套件带 --cov 约需 7 分钟，三平台各跑一遍没有增量信息），见 .github/workflows/ci.yml。

用法：
    uv run python scripts/check_coverage.py                    # 与 baseline 比较
    uv run python scripts/check_coverage.py --write-baseline   # 首次/重置 baseline
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_BASELINE = _ROOT / "coverage-baseline.json"
_REPORT_PATH = _ROOT / ".nexuscli" / "coverage.json"
_COVERAGE_FILE = _ROOT / ".nexuscli" / ".coverage"
_COV_ENV_DIR = _ROOT / ".nexuscli" / "cov-venv"
_PYTHON_PIN = "3.11"

# 内层 pytest 固定走 uv + Python 3.11 + dev extras（pytest-cov 在 dev extras 里），
# 与 CI 的门禁矩阵项同一条命令。_PYTEST_DISPLAY 是同一命令的仓库相对展示形
# （写进 baseline，不能携带本机绝对路径），两处需同步修改。
_UV_RUN_ARGS = [
    "uv",
    "run",
    "--python",
    _PYTHON_PIN,
    "--extra",
    "dev",
    "--frozen",
]
_PYTEST_ARGS = [
    *_UV_RUN_ARGS,
    "python",
    "-m",
    "pytest",
    "--cov=nexuscli",
    f"--cov-report=json:{_REPORT_PATH}",
]
_PYTEST_DISPLAY = (
    "uv run --python 3.11 --extra dev --frozen python -m pytest "
    "--cov=nexuscli --cov-report=json:.nexuscli/coverage.json"
)

# totals.percent_covered 是两条整数行数的比值，同一代码状态下可精确复现；
# epsilon 只吸收浮点表示噪声，不做任何人工放宽。
_EPSILON = 1e-9


def _cov_env() -> dict:
    """内层 uv run 的环境：独立 project env，不触碰仓库 .venv。"""

    env = dict(os.environ)
    # 外层 `uv run` 注入的 VIRTUAL_ENV 指向仓库 .venv，与独立 env 冲突时 uv
    # 只是告警并忽略；摘掉它让内层环境选择干净。
    env.pop("VIRTUAL_ENV", None)
    env["UV_PROJECT_ENVIRONMENT"] = str(_COV_ENV_DIR)
    # coverage 原始数据默认写在进程 cwd（--cov-report 的路径不控制它）；不钉住
    # 的话 .coverage 会落在仓库根，与「不在仓库根留残渣」相悖。
    env["COVERAGE_FILE"] = str(_COVERAGE_FILE)
    return env


def _pinned_python_version() -> str:
    """实测钉定解释器的版本号（写进 baseline 的 python 字段）。"""

    completed = subprocess.run(
        [*_UV_RUN_ARGS, "python", "-c", "import platform; print(platform.python_version())"],
        cwd=_ROOT,
        env=_cov_env(),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise SystemExit(
            f"无法确定 Python {_PYTHON_PIN} 解释器版本（exit {completed.returncode}）："
            f"{completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def _current_platform() -> str:
    """当前生成/比对平台（写进 baseline 的 platform 字段）。

    只取 ``sys.platform`` + 机器架构（如 ``darwin-arm64`` / ``linux-x86_64``）：
    稳定、可读，恰好覆盖 macOS 本机生成 vs ubuntu CI 比对这一分支覆盖差异
    来源；不带 OS 版本号，避免 CI 镜像升级让警告变噪声。
    """

    return f"{sys.platform}-{platform.machine()}"


def _run_pytest() -> float:
    """跑全量套件并从 JSON 报告读出 total percent。"""

    completed = subprocess.run(_PYTEST_ARGS, cwd=_ROOT, env=_cov_env(), check=False)
    if completed.returncode != 0:
        # 测试本身失败时不要拿覆盖率说事：按 pytest 的退出码原样失败。
        raise SystemExit(completed.returncode)
    try:
        report = json.loads(_REPORT_PATH.read_text(encoding="utf-8"))
        return float(report["totals"]["percent_covered"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f"coverage 报告缺失或格式不对（{_REPORT_PATH}）：{exc}") from exc


def _load_baseline(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(
            f"baseline 不存在：{path}\n"
            "先跑 `uv run python scripts/check_coverage.py --write-baseline` 生成，"
            "并把 coverage-baseline.json 提交进仓库。"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        # 与其余损坏形态同口径的友好退出：裸 traceback 对 CI 日志里的定位没有帮助。
        raise SystemExit(
            f"baseline 不是合法 JSON：{path}（{exc}）\n"
            "先跑 `uv run python scripts/check_coverage.py --write-baseline` 生成，"
            "并把 coverage-baseline.json 提交进仓库。"
        ) from exc
    percent = data.get("percent") if isinstance(data, dict) else None
    if not isinstance(percent, (int, float)) or isinstance(percent, bool):
        raise SystemExit(f"baseline 缺少数值型 percent 字段：{path}")
    return data


def _write_baseline(path: Path, percent: float) -> None:
    baseline = {
        "percent": percent,
        "python": _pinned_python_version(),
        "python_pin": _PYTHON_PIN,
        "platform": _current_platform(),
        "command": "uv run python scripts/check_coverage.py --write-baseline",
        "pytest_command": _PYTEST_DISPLAY,
    }
    path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"baseline 已写入 {path}（percent={percent:.2f}, python={baseline['python']}, "
        f"platform={baseline['platform']}）"
    )


def _check_platform(baseline: dict) -> None:
    """平台不一致只警告不失败。

    baseline 记录生成平台：本机（如 macOS）生成的基线拿到另一个平台（如
    ubuntu CI）上比对，分支覆盖的小幅差异可能让棘轮首跑误红——该前提无法
    被门禁自身校验，所以不一致时打印一行警告提示「数字可能不可比，必要时
    重新 --write-baseline」，但不让 CI 因此常红。旧 baseline 没有 platform
    字段时保持沉默（字段缺失不构成失败信号）。
    """

    recorded = baseline.get("platform")
    if not isinstance(recorded, str) or not recorded:
        return
    current = _current_platform()
    if recorded != current:
        print(
            f"WARNING: coverage baseline 生成于 {recorded}，当前比对平台是 {current}；"
            "跨平台分支覆盖可能有小幅差异，数字系统性漂移时请在本机重新 --write-baseline"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="覆盖率棘轮：低于 coverage-baseline.json 即 exit 1。",
        epilog=f"内层命令：{_PYTEST_DISPLAY}",
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="用本次实测覆盖率写入/重置 baseline（首次接入或有意放宽时使用）",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=_DEFAULT_BASELINE,
        help="baseline 文件路径（默认仓库根 coverage-baseline.json）",
    )
    args = parser.parse_args(argv)

    percent = _run_pytest()
    print(f"coverage total: {percent:.2f}%")

    if args.write_baseline:
        _write_baseline(args.baseline, percent)
        return 0

    baseline = _load_baseline(args.baseline)
    _check_platform(baseline)
    floor = float(baseline["percent"])
    delta = percent - floor
    if percent + _EPSILON < floor:
        print(f"FAIL: coverage {percent:.2f}% 低于 baseline {floor:.2f}%（{delta:+.2f}%）")
        return 1
    print(f"OK: coverage {percent:.2f}% ≥ baseline {floor:.2f}%（{delta:+.2f}%）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
