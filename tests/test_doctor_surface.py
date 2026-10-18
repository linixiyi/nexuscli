"""doctor surface — matrix row 1.7 (was 🟠: real-run only).

``nexuscli doctor`` is the first command a fresh install runs. The output is a
JSON object printed by ``cli.doctor`` (src/nexuscli/entrypoints/cli.py:327);
this file pins that the five environment sections a user actually checks —
Python, uv, Node, ripgrep, API key — stay present and machine-readable.

Whether each tool is installed on the running machine is environment, not
contract: tool values are asserted readable (a path/version string or the
literal "missing"), never asserted present.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_BIN_DIR = "Scripts" if sys.platform == "win32" else "bin"
NEXUSCLI_ENTRY = REPO_ROOT / ".venv" / _BIN_DIR / "nexuscli"

requires_cli_entry = pytest.mark.skipif(
    not NEXUSCLI_ENTRY.exists(),
    reason=".venv CLI entry point not built (run `uv sync` first)",
)


@requires_cli_entry
def test_doctor_prints_json_with_the_five_env_sections() -> None:
    # A real subprocess against the built entry point, exactly what a user runs
    # after installing; stdout must parse as JSON on its own.
    proc = subprocess.run(
        [str(NEXUSCLI_ENTRY), "doctor"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    checks = json.loads(proc.stdout)

    # 1. python — a readable version string.
    assert re.fullmatch(r"\d+\.\d+\.\d+", str(checks["python"]))
    # 2/3/4. uv / node / rg — readable whether installed or not; the ask
    # forbids hard-asserting the environment has them.
    for tool in ("uv", "node", "npx", "rg"):
        value = checks[tool]
        assert isinstance(value, str) and value.strip(), f"{tool} value unreadable: {value!r}"
    # 5. api key — the configured/missing verdict, never the key itself.
    assert checks["api_key"] in {"configured", "missing"}

    # Context rows doctor also reports so /config's "Full dump" pointer holds.
    assert checks["cwd"] == str(REPO_ROOT.resolve())
    assert isinstance(checks["config_paths"], list) and len(checks["config_paths"]) == 2
