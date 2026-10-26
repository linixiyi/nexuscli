"""Run the full workbench UX journey and capture per-step screenshots.

Ported from the review-session driver /tmp/ux-journey.mjs
(2026-10-27_workbench-ux-iteration): the journey half moves into
scripts/ui_journey_runner.mjs (same step names as the baseline evidence in
.spec/specs/2026-10-27_workbench-ux-iteration/evidence/baseline/), and this
entrypoint owns the server lifecycle so the journey is one command:

  python3 scripts/ui_journey.py --out <dir> [--slow]

Round-trip:
- spawns scripts/web_validation_server.py (offline demo provider; one READY
  JSON frame on stdout carrying the one-use bootstrap URL — never logged),
- hands {origin, bootstrap_url, out, requireBase} to the runner through a temp
  plan file (deleted on exit; same channel as ui_compare_shots.py),
- shuts the server down and waits for the process group to be truly gone
  (AGENTS.md 进程守则: start_new_session + killpg, no premature "stopped").

Screenshot layout matches /tmp/ux-review exactly: flat NN-name.png files, so
baseline/ vs after/ comparisons stay per-file (findings.md references them by
name).
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
READY_TIMEOUT = 30.0


def start_server(slow: bool) -> subprocess.Popen:
    argv = ["uv", "run", "python", "scripts/web_validation_server.py"]
    if slow:
        argv.append("--slow")
    return subprocess.Popen(
        argv,
        cwd=REPO,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,  # AGENTS.md 进程守则：独立进程组，killpg 清整组
    )


def wait_ready(proc: subprocess.Popen) -> dict:
    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline:
        line = proc.stdout.readline()  # type: ignore[union-attr]
        if not line:
            raise RuntimeError("validation server exited before READY")
        if line.startswith("READY "):
            return json.loads(line[len("READY ") :])
    raise RuntimeError("validation server READY timeout")


def stop_server(proc: subprocess.Popen) -> None:
    """stdin 换行请求退出（validation server 协议）；退化路径 killpg 整组。

    等进程组真正清空才返回：SIGTERM 后先 wait，再以 killpg(pgid, 0) 探测组内存活，
    孙进程（uv→python）未清空时升级 SIGKILL。
    """
    if proc.poll() is not None:
        return
    try:
        if proc.stdin is not None:
            proc.stdin.write("\n")
            proc.stdin.flush()
        proc.wait(timeout=10)
    except Exception:
        pass
    pgid = proc.pid  # start_new_session → 进程组 id == 子进程 pid
    for sig, grace in ((signal.SIGTERM, 3), (signal.SIGKILL, 3)):
        if proc.poll() is not None:
            break
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            break
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            continue
    # 组内残余（孙进程）探测：仍存活 → 最后一次 SIGKILL 后短暂等待
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default="/tmp/ux-review",
        help="screenshot output directory (default /tmp/ux-review, layout matches baseline)",
    )
    parser.add_argument(
        "--slow",
        action="store_true",
        help="slow the demo provider's streaming for human observation",
    )
    args = parser.parse_args()

    out_dir = Path(args.out).resolve()
    proc = start_server(args.slow)
    plan_path = ""
    try:
        ready = wait_ready(proc)
        print(f"validation server ready: {ready['origin']}", flush=True)
        plan = {
            "origin": ready["origin"],
            "bootstrap_url": ready["bootstrap_url"],
            "out": str(out_dir),
            "requireBase": str(REPO / "frontend" / "package.json"),
        }
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, prefix="ui-journey-plan-"
        ) as handle:
            json.dump(plan, handle)
            plan_path = handle.name

        runner = REPO / "scripts" / "ui_journey_runner.mjs"
        result = subprocess.run(
            ["node", str(runner), plan_path],
            cwd=REPO / "frontend",
            env={**os.environ, "NODE_PATH": str(REPO / "frontend" / "node_modules")},
        )
        print(f"screenshots: {out_dir}", flush=True)
        return result.returncode
    finally:
        if plan_path:
            # plan 文件携带一次性口令，用完即删（ui_compare_shots.py 同款规则）
            Path(plan_path).unlink(missing_ok=True)
        stop_server(proc)


if __name__ == "__main__":
    sys.exit(main())
