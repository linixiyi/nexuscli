#!/usr/bin/env python3
"""mypy 棘轮：跑钉定版本的 mypy，对比 mypy-baseline.json，任一指标变差即 exit 1。

设计（task_0023）：
- 棘轮不是目标：baseline 由 `--write-baseline` 按当前实测值生成；之后错误数、
  ignore_errors 模块数、`# type: ignore` 行数任一**变多**就红，变好不奖励、
  不设归零目标。三个指标分别堵住三条退路：新增类型错误、往
  [tool.mypy.overrides] 排除清单塞模块（清单纪律「只减不增」）、用行内
  ignore 注释压错误。
- mypy 版本钉在 pyproject.toml dev extras（`mypy==X.Y.Z`）：错误数随 mypy 版本
  波动，跨版本比较 baseline 没有意义。脚本从这里读 pin 生成内层命令，版本与
  baseline 不一致时直接失败并提示重新生成（有意升级才放宽）。
- 内层 mypy 钉在 Python 3.11 独立 env（.nexuscli/mypy-venv，已 gitignore，不动
  仓库 .venv）：mypy 依据运行解释器解析第三方包类型，环境不同错误数不可比；
  [tool.mypy] 的 python_version 也钉在 3.11。CI 门禁跑同一个脚本，与本地同口径。
- mypy 原始输出（错误明细 + 摘要）原样回显：棘轮红时可直接定位到文件行，
  摘要解析失败也带着原始输出报错，不静默吞掉。mypy 缓存放
  .nexuscli/mypy_cache（.nexuscli/ 已 gitignore），不在仓库根留残渣。
- baseline 记录 mypy/Python 版本与生成命令：换版本/环境后数字不可比，应重新
  `--write-baseline`。

用法：
    uv run python scripts/check_mypy_ratchet.py                    # 与 baseline 比较
    uv run python scripts/check_mypy_ratchet.py --write-baseline   # 首次/重置 baseline
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_BASELINE = _ROOT / "mypy-baseline.json"
_PYPROJECT = _ROOT / "pyproject.toml"
_SRC_DIR = _ROOT / "src" / "nexuscli"
_MYPY_ENV_DIR = _ROOT / ".nexuscli" / "mypy-venv"
_CACHE_DIR = _ROOT / ".nexuscli" / "mypy_cache"
_PYTHON_PIN = "3.11"

# 行内忽略注释：统计 src/nexuscli 下含 `# type: ignore`（含 `ignore[code]` 变体）
# 的行数。按源码行计数而非 mypy 实际压掉的数量——后者随 mypy 版本漂移，会让
# 棘轮在没有代码变化时误报；前者只随源码变化，信号稳定（宁可偏严不算偏松）。
_IGNORE_LINE_RE = re.compile(r"#\s*type:\s*ignore")

# mypy 摘要两种形态：有错误 `Found N errors in M files (checked K source files)`，
# 无错误 `Success: no issues found in K source files`。
_SUMMARY_RE = re.compile(r"Found (\d+) errors? in \d+ files? \(checked \d+ source files?\)")
_SUCCESS_RE = re.compile(r"Success: no issues found in \d+ source files")

_METRICS = ("errors", "ignored_modules", "ignored_lines")


def _mypy_env() -> dict:
    """内层 uv run 的环境：独立 project env，不触碰仓库 .venv。"""

    env = dict(os.environ)
    # 外层 `uv run` 注入的 VIRTUAL_ENV 指向仓库 .venv，摘掉让内层环境选择干净。
    env.pop("VIRTUAL_ENV", None)
    env["UV_PROJECT_ENVIRONMENT"] = str(_MYPY_ENV_DIR)
    return env


def _read_mypy_pin() -> str:
    """读 pyproject dev extras 里钉死的 mypy 版本（单一事实来源）。"""

    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    dev = data.get("project", {}).get("optional-dependencies", {}).get("dev", [])
    if not isinstance(dev, list):
        raise SystemExit(f"pyproject.toml 的 dev extras 不是列表：{_PYPROJECT}")
    exact = [req for req in dev if isinstance(req, str) and req.strip().startswith("mypy==")]
    if exact:
        return exact[0].strip().removeprefix("mypy==")
    if any(isinstance(req, str) and req.strip() == "mypy" for req in dev):
        raise SystemExit(
            "pyproject.toml dev extras 里的 mypy 是裸依赖：错误数随 mypy 版本波动，"
            "棘轮要求钉死为 `mypy==X.Y.Z`（当前版本见 `uv run --with mypy mypy --version`）。"
        )
    raise SystemExit(
        "pyproject.toml dev extras 未声明 mypy：请加入 `mypy==X.Y.Z`，再重新 `--write-baseline`。"
    )


def _uv_run_args(pin: str) -> list[str]:
    return ["uv", "run", "--python", _PYTHON_PIN, "--frozen", "--with", f"mypy=={pin}"]


def _mypy_command_display(pin: str) -> str:
    """内层命令的仓库相对展示形（写进 baseline，不携带本机绝对路径）。"""

    return (
        f"uv run --python {_PYTHON_PIN} --frozen --with mypy=={pin} "
        "mypy src/nexuscli --cache-dir=.nexuscli/mypy_cache"
    )


def _probe_versions(pin: str) -> tuple[str, str]:
    """实测内层环境的 Python 与 mypy 版本号（写进 baseline 的 provenance 字段）。"""

    completed = subprocess.run(
        [
            *_uv_run_args(pin),
            "python",
            "-c",
            "import platform, mypy.version; "
            "print(platform.python_version(), mypy.version.__version__)",
        ],
        cwd=_ROOT,
        env=_mypy_env(),
        check=False,
        capture_output=True,
        text=True,
    )
    parts = completed.stdout.split()
    if completed.returncode != 0 or len(parts) < 2:
        raise SystemExit(
            f"无法确定内层 mypy/Python 版本（exit {completed.returncode}）："
            f"{completed.stderr.strip()}"
        )
    return parts[0], parts[1]


def _run_mypy(pin: str) -> int:
    """跑 mypy，原样回显原始输出，返回解析出的错误数。"""

    command = [*_uv_run_args(pin), "mypy", "src/nexuscli", f"--cache-dir={_CACHE_DIR}"]
    completed = subprocess.run(
        command, cwd=_ROOT, env=_mypy_env(), check=False, capture_output=True, text=True
    )
    # 保留原始输出便于定位（诊断在 stdout，uv 的安装噪声在 stderr）。
    sys.stderr.write(completed.stderr)
    sys.stdout.write(completed.stdout)
    if completed.returncode not in (0, 1):
        raise SystemExit(
            f"mypy 异常退出（exit {completed.returncode}），无法取摘要；原始输出如上。"
        )
    match = _SUMMARY_RE.search(completed.stdout)
    if match:
        return int(match.group(1))
    if completed.returncode == 0 and _SUCCESS_RE.search(completed.stdout):
        return 0
    raise SystemExit(f"无法从 mypy 输出解析错误摘要（exit {completed.returncode}），原始输出如上。")


def _count_ignore_modules() -> int:
    """统计 [tool.mypy.overrides] 里 ignore_errors=true 覆盖的模块数。"""

    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    overrides = data.get("tool", {}).get("mypy", {}).get("overrides", [])
    total = 0
    for override in overrides:
        if override.get("ignore_errors") is not True:
            continue
        modules = override.get("module", [])
        total += 1 if isinstance(modules, str) else len(modules)
    return total


def _count_ignored_lines() -> int:
    """统计 src/nexuscli 下含 `# type: ignore` 注释的源码行数。"""

    total = 0
    for path in sorted(_SRC_DIR.rglob("*.py")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if _IGNORE_LINE_RE.search(line):
                total += 1
    return total


def _load_baseline(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(
            f"baseline 不存在：{path}\n"
            "先跑 `uv run python scripts/check_mypy_ratchet.py --write-baseline` 生成，"
            "并把 mypy-baseline.json 提交进仓库。"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        # 与其余损坏形态同口径的友好退出：裸 traceback 对 CI 日志里的定位没有帮助。
        raise SystemExit(
            f"baseline 不是合法 JSON：{path}（{exc}）\n"
            "先跑 `uv run python scripts/check_mypy_ratchet.py --write-baseline` 生成，"
            "并把 mypy-baseline.json 提交进仓库。"
        ) from exc
    if not isinstance(data, dict):
        raise SystemExit(f"baseline 顶层不是对象：{path}")
    for key in (*_METRICS, "mypy", "mypy_pin", "python", "python_pin"):
        if key not in data:
            raise SystemExit(f"baseline 缺少 {key} 字段：{path}")
    for key in _METRICS:
        value = data[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise SystemExit(f"baseline 的 {key} 必须是非负整数：{path}")
    return data


def _write_baseline(
    path: Path, *, errors: int, ignored_modules: int, ignored_lines: int, pin: str
) -> None:
    python_version, mypy_version = _probe_versions(pin)
    if mypy_version != pin:
        raise SystemExit(
            f"内层实际 mypy 版本 {mypy_version} 与 dev extras 钉定的 {pin} 不一致，"
            "拒绝写 baseline。"
        )
    baseline = {
        "errors": errors,
        "ignored_modules": ignored_modules,
        "ignored_lines": ignored_lines,
        "mypy": mypy_version,
        "mypy_pin": pin,
        "python": python_version,
        "python_pin": _PYTHON_PIN,
        "command": "uv run python scripts/check_mypy_ratchet.py --write-baseline",
        "mypy_command": _mypy_command_display(pin),
    }
    path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"baseline 已写入 {path}（errors={errors}, ignored_modules={ignored_modules}, "
        f"ignored_lines={ignored_lines}, mypy={mypy_version}, python={python_version}）"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="mypy 棘轮：任一指标高于 mypy-baseline.json 即 exit 1。",
        epilog="内层命令由 pyproject dev extras 的 mypy==pin 生成，Python 钉在 3.11。",
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="用本次实测指标写入/重置 baseline（首次接入或有意放宽时使用）",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=_DEFAULT_BASELINE,
        help="baseline 文件路径（默认仓库根 mypy-baseline.json）",
    )
    args = parser.parse_args(argv)

    pin = _read_mypy_pin()

    if args.write_baseline:
        _write_baseline(
            args.baseline,
            errors=_run_mypy(pin),
            ignored_modules=_count_ignore_modules(),
            ignored_lines=_count_ignored_lines(),
            pin=pin,
        )
        return 0

    baseline = _load_baseline(args.baseline)
    # 版本不匹配时错误数不可比，先于 mypy 运行快速失败，提示走显式放宽流程。
    if baseline["mypy"] != pin:
        print(
            f"FAIL: 内层 mypy 钉定版本 {pin} 与 baseline 记录的 {baseline['mypy']} 不同，"
            "错误数跨版本不可比；确认升级有意后用 --write-baseline 重新生成 baseline。"
        )
        return 1
    if baseline["python_pin"] != _PYTHON_PIN:
        print(
            f"FAIL: 脚本 Python pin {_PYTHON_PIN} 与 baseline 记录的 "
            f"{baseline['python_pin']} 不同，指标跨解释器不可比；同上重新生成 baseline。"
        )
        return 1

    errors = _run_mypy(pin)
    current = {
        "errors": errors,
        "ignored_modules": _count_ignore_modules(),
        "ignored_lines": _count_ignored_lines(),
    }

    regressions = []
    for key in _METRICS:
        value, floor = current[key], int(baseline[key])
        if value > floor:
            regressions.append(f"{key} {value} > baseline {floor}")
        else:
            print(f"OK: {key} {value} ≤ baseline {floor}（{value - floor:+d}）")
    if regressions:
        print("FAIL: " + "；".join(regressions))
        return 1
    print("OK: mypy 棘轮通过（错误数 / ignore_errors 模块数 / type: ignore 行数均未变差）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
