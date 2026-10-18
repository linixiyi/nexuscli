"""Plain exits and pasted input — matrix rows 1.9, 3.5, 3.6, 3.10 (were 🟠).

Four surfaces that only had real-run evidence:

* ``--version`` must print the package version dynamically (a release bump
  must not have to touch this test);
* Ctrl+C on an empty prompt must exit 0 (not die on SIGINT);
* Ctrl+D on an empty prompt must exit 0;
* a bracketed paste carrying newlines must reach the REPL with the newlines
  intact (the paste is routed to ``/save`` so the stored memory proves what
  the REPL submitted — no gateway involved).

The exit/paste cases drive the real entry point over a pty with a throwaway
HOME and a base_url pinned to a closed local port, so no test can leak a real
LLM request even if a keystroke slips through.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

import nexuscli

REPO_ROOT = Path(__file__).resolve().parents[1]
_BIN_DIR = "Scripts" if sys.platform == "win32" else "bin"
NEXUSCLI_ENTRY = REPO_ROOT / ".venv" / _BIN_DIR / "nexuscli"

requires_cli_entry = pytest.mark.skipif(
    not NEXUSCLI_ENTRY.exists(),
    reason=".venv CLI entry point not built (run `uv sync` first)",
)

if sys.platform != "win32":
    # Platform-guarded imports: fcntl/termios do not exist on Windows, and
    # these names are only touched by the pty tests below (skipped there).
    import fcntl
    import pty
    import select
    import struct
    import termios

PTY_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="pty is POSIX-only")

PROMPT_READY = "Type your message"
PASTE_START = b"\x1b[200~"
PASTE_END = b"\x1b[201~"


def _spawn_repl(workdir: Path, home: Path):
    """Start the real REPL on a fresh pty, fully isolated from the real world.

    The throwaway HOME keeps sessions/memory/history out of the developer's
    ~/.nexuscli; the config pins a stub key and a base_url on a closed local
    port so any accidental agent turn fails instantly without network egress.
    """
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 200, 0, 0))
    config_dir = home / ".nexuscli"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "llm": {
                    "provider": "openai-compatible",
                    "model": "stub-model",
                    "base_url": "http://127.0.0.1:9/v1",
                    "api_key": "stub-key-not-real",
                }
            }
        ),
        encoding="utf-8",
    )
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "USERPROFILE": str(home),
        "TERM": "xterm-256color",
        "LANG": "en_US.UTF-8",
    }
    proc = subprocess.Popen(
        [str(NEXUSCLI_ENTRY)],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=str(workdir),
        env=env,
        preexec_fn=os.setsid,
        close_fds=True,
    )
    os.close(slave)
    return proc, master


def _read_until(master: int, needle: str, timeout: float = 30.0) -> None:
    """Read the pty until ``needle`` appears in *new* output.

    Every call starts from the current fd cursor, so waiting for the same
    marker twice (e.g. the prompt placeholder re-rendered after a command)
    only matches output produced after the first wait. Fails with the
    collected output instead of hanging.
    """
    buf = b""
    deadline = time.monotonic() + timeout
    while needle.encode() not in buf:
        if time.monotonic() > deadline:
            tail = buf.decode("utf-8", "replace")[-2000:]
            raise AssertionError(f"pty output never showed {needle!r}; got:\n{tail}")
        ready, _, _ = select.select([master], [], [], 0.2)
        if not ready:
            continue
        try:
            chunk = os.read(master, 65536)
        except OSError:
            break
        if not chunk:
            break
        buf += chunk


def _stop(proc: subprocess.Popen, master: int) -> None:
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=10)
    os.close(master)


@requires_cli_entry
def test_version_prints_the_package_version_dynamically() -> None:
    # Matrix 1.9. Dynamic on purpose: the release bump to 0.2.0 must keep this
    # green without touching the test (the literal is never pinned here).
    proc = subprocess.run(
        [str(NEXUSCLI_ENTRY), "--version"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == f"nexuscli {nexuscli.__version__}"
    assert re.fullmatch(r"nexuscli \d+\.\d+\.\d+", proc.stdout.strip())


@PTY_ONLY
@requires_cli_entry
def test_ctrl_c_on_empty_prompt_exits_zero(tmp_path: Path) -> None:
    # Matrix 3.5. Ctrl+C at the prompt is owned by prompt_toolkit and must
    # unwind the REPL cleanly: exit code 0, not death by SIGINT (-2).
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir()
    work.mkdir()
    proc, master = _spawn_repl(work, home)
    try:
        _read_until(master, PROMPT_READY)
        os.write(master, b"\x03")
        assert proc.wait(timeout=25) == 0
    finally:
        _stop(proc, master)


@PTY_ONLY
@requires_cli_entry
def test_ctrl_d_on_empty_prompt_exits_zero(tmp_path: Path) -> None:
    # Matrix 3.6. The conventional exit key: EOF on an empty buffer must exit
    # 0 just like /exit does.
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir()
    work.mkdir()
    proc, master = _spawn_repl(work, home)
    try:
        _read_until(master, PROMPT_READY)
        os.write(master, b"\x04")
        assert proc.wait(timeout=25) == 0
    finally:
        _stop(proc, master)


@PTY_ONLY
@requires_cli_entry
def test_bracketed_paste_keeps_multiline_newlines(tmp_path: Path) -> None:
    # Matrix 3.10. Paste two lines wrapped in bracketed-paste markers; the
    # submitted text must keep the internal newline. /save is the receiver:
    # the stored memory row is durable proof of what the REPL handed over,
    # and a slash command never touches the (stubbed) gateway.
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir()
    work.mkdir()
    proc, master = _spawn_repl(work, home)
    try:
        _read_until(master, PROMPT_READY)
        os.write(master, PASTE_START + b"/save alpha line\nbeta line" + PASTE_END)
        # Wait for the echo before Enter: proves the paste landed in the
        # buffer as two lines and closes the keystroke race that once hung a
        # manual probe (Enter must never arrive mid-paste).
        _read_until(master, "beta line")
        time.sleep(0.5)
        os.write(master, b"\r")
        _read_until(master, "Saved memory #")
        # The prompt must be re-armed before Ctrl+D: wait for the placeholder
        # to re-render in output newer than the save confirmation.
        _read_until(master, PROMPT_READY)
        time.sleep(0.5)
        os.write(master, b"\x04")
        assert proc.wait(timeout=25) == 0
    finally:
        _stop(proc, master)

    db_path = home / ".nexuscli" / "memory.db"
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("select content from memories").fetchall()
    finally:
        conn.close()
    assert ("alpha line\nbeta line",) in rows
