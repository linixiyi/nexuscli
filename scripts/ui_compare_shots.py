"""Launch both workbench demo stacks (NexusCLI + kitagent) and capture
side-by-side Playwright screenshots for the UI alignment comparison.

Round-trip (2026-10-26_workbench-kitagent-ui task_0002/task_0005): the
self-iteration loop is "run both real UIs → screenshot → fix gaps → re-shoot";
this script is the "run and screenshot" half.

Sources:
- NexusCLI side: scripts/web_validation_server.py (offline demo provider, one
  READY JSON frame on stdout carrying the one-use bootstrap URL; stdin line
  requests graceful shutdown).
- kitagent side (MIT, zed123214/kitagent): its own demo stack —
  ``python -m agent_runtime.web.demo`` (scripted Core) + ``agentrt-web``
  (gateway; prints the "Open http://.../#bootstrap=..." line on stdout).

Screenshots are taken by scripts/ui_shots_runner.mjs via the frontend's
Playwright dependency (the repo has no Python playwright).

Usage:
  python3 scripts/ui_compare_shots.py --out <dir> [--side both|nexuscli|kitagent] [--final]

--final names the subdirectory final/ instead of baseline/ (task_0005 gate).
Never log the bootstrap tokens: they are one-use capabilities; the script
passes them to the browser runner through a temp JSON file deleted on exit.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
KITAGENT_SRC = Path("/tmp/kitagent-src")
READY_TIMEOUT = 30.0


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def start_nexuscli() -> tuple[dict, subprocess.Popen]:
    """Start the NexusCLI validation server; return (info, process)."""
    proc = subprocess.Popen(
        ["uv", "run", "python", "scripts/web_validation_server.py"],
        cwd=REPO,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline:
        line = proc.stdout.readline()  # type: ignore[union-attr]
        if not line:
            raise RuntimeError("nexuscli validation server exited before READY")
        if line.startswith("READY "):
            return json.loads(line[len("READY ") :]), proc
    raise RuntimeError("nexuscli validation server READY timeout")


def start_kitagent(root: Path) -> tuple[dict, list[subprocess.Popen]]:
    """Start kitagent demo core + web gateway; return (info, processes)."""
    py = KITAGENT_SRC / ".venv" / "bin" / "python"
    web = KITAGENT_SRC / ".venv" / "bin" / "agentrt-web"
    if not py.exists() or not web.exists():
        raise RuntimeError(
            f"kitagent venv missing under {KITAGENT_SRC}; re-clone and uv sync --extra web"
        )
    demo_parent = root / "kitagent"
    demo_parent.mkdir(exist_ok=False)
    # demo.py creates the workspace itself (mkdir exist_ok=False) and seeds
    # README/check.py — pass a non-existent path, never pre-create it.
    demo_dir = demo_parent / "demo"
    core_port, web_port = free_port(), free_port()
    core = subprocess.Popen(
        [
            str(py),
            "-m",
            "agent_runtime.web.demo",
            "--port",
            str(core_port),
            "--directory",
            str(demo_dir),
        ],
        cwd=KITAGENT_SRC,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    gateway = subprocess.Popen(
        [str(web), "--port", str(web_port), "--core-port", str(core_port)],
        cwd=KITAGENT_SRC,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + READY_TIMEOUT
    link = ""
    while time.monotonic() < deadline:
        line = gateway.stdout.readline()  # type: ignore[union-attr]
        if not line:
            raise RuntimeError("kitagent gateway exited before printing its link")
        match = re.search(r"#bootstrap=(\S+)", line)
        if match:
            link = line.strip().split(" ", 1)[1] if " " in line.strip() else line.strip()
            if not link.startswith("http"):
                link = f"http://127.0.0.1:{web_port}/#bootstrap={match.group(1)}"
            break
    if not link:
        raise RuntimeError("kitagent gateway link timeout")
    return {"origin": f"http://127.0.0.1:{web_port}", "bootstrap_url": link}, [gateway, core]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="screenshot output directory")
    parser.add_argument("--side", choices=["both", "nexuscli", "kitagent"], default="both")
    parser.add_argument("--final", action="store_true", help="write into final/ subdirectory")
    parser.add_argument("--keep", action="store_true", help="keep servers running (debug)")
    args = parser.parse_args()

    out_dir = Path(args.out).resolve()
    out_dir = out_dir / ("final" if args.final else "baseline")
    out_dir.mkdir(parents=True, exist_ok=True)

    plan: dict = {
        "out": str(out_dir),
        "side": args.side,
        "requireBase": str(REPO / "frontend" / "package.json"),
        "sides": {},
    }
    nexuscli_proc = None
    kitagent_procs: list[subprocess.Popen] = []
    root = tempfile.TemporaryDirectory(prefix="nexuscli-ui-compare-")
    try:
        if args.side in ("both", "nexuscli"):
            info, nexuscli_proc = start_nexuscli()
            plan["sides"]["nexuscli"] = info
            print(f"nexuscli ready: {info['origin']}", flush=True)
        if args.side in ("both", "kitagent"):
            info, kitagent_procs = start_kitagent(Path(root.name))
            plan["sides"]["kitagent"] = info
            print(f"kitagent ready: {info['origin']}", flush=True)

        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, prefix="ui-shots-plan-"
        ) as handle:
            json.dump(plan, handle)
            plan_path = handle.name

        runner = REPO / "scripts" / "ui_shots_runner.mjs"
        result = subprocess.run(
            ["node", str(runner), plan_path],
            cwd=REPO / "frontend",
            env={**os.environ, "NODE_PATH": str(REPO / "frontend" / "node_modules")},
        )
        return result.returncode
    finally:
        if plan.get("sides"):
            # The plan file carried one-use bootstrap tokens; do not leave it around.
            Path(plan_path).unlink(missing_ok=True)
        if nexuscli_proc is not None and nexuscli_proc.stdin is not None and not args.keep:
            try:
                nexuscli_proc.stdin.write("\n")
                nexuscli_proc.stdin.flush()
                nexuscli_proc.wait(timeout=10)
            except Exception:
                nexuscli_proc.kill()
        for proc in kitagent_procs:
            if not args.keep:
                proc.terminate()
        root.cleanup()


if __name__ == "__main__":
    sys.exit(main())
