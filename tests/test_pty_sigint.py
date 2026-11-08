"""Real-REPL pty review for SIGINT delivery (spec 2026-11-07 task_0024).

The turn-mid Ctrl+C contract (``repl.py`` ``_arm_turn_sigint``) was fixed and
pty-verified by hand in an earlier round, but the automated pty coverage in
``test_plain_exits.py`` only pressed Ctrl+C at an *empty prompt*, and — per
AGENTS.md — a bare pty test that never answers the ``ESC[6n`` cursor-position
query runs with a mis-positioned prompt_toolkit, so a green result there can
be a false conclusion. This file drives the real CLI entry point over a
**self-made pty that answers every CPR query** and then sends a real SIGINT:

* **mid-turn** (offline demo turn still streaming): the SIGINT must abort the
  turn — "Interrupted — turn aborted." — while the process stays alive, the
  prompt re-arms, and a subsequent ``/exit`` leaves exit code 0. The failure
  modes being pinned: SIG_DFL after prompt_toolkit's
  ``remove_signal_handler`` teardown kills the process outright (exit -2), and
  a handler that raises ``KeyboardInterrupt`` gets ``asyncio.run`` converted
  into ``SystemExit(130)`` — neither may reappear.
* **at the empty prompt**: prompt_toolkit owns the terminal there; Ctrl+C must
  still unwind to a clean exit 0 (never 130, never signal death).

Hermetic by construction (same shape as ``test_plain_exits.py``): throwaway
HOME, config ``base_url`` pinned to a closed local port, and the LLM replaced
by the scripted ``OfflineDemoProvider`` via ``NEXUSCLI_OFFLINE_DEMO=1`` — no
gateway is ever contacted. ``NEXUSCLI_OFFLINE_DEMO_INITIAL_DELAY`` paces the
demo's 8-char text chunks so the first turn streams for several seconds,
leaving a wide interrupt window that does not depend on machine speed: the
driver waits for a phrase that only appears once the *second* chunk rendered
(one full pacing sleep already elapsed) and fires well before the remaining
chunks and follow-up turns could drain the window.

Delivery path note (probed on macOS arm64, 2026-11-08): mid-turn the terminal
is back in canonical mode, so Ctrl+C reaches the child through the tty line
discipline's ISIG only if the child actually owns the pty as its controlling
terminal — ``os.setsid()`` alone is not enough (without ``TIOCSCTTY`` the
``\\x03`` byte silently sits in the input buffer until the next prompt reads
it as a keystroke). The child setup therefore does ``setsid`` +
``TIOCSCTTY`` before exec.

Windows: skipped entirely — openpty/fcntl/termios and the ISIG delivery path
are POSIX-only (the reason is stated in the skip marker, not just implied).
"""

from __future__ import annotations

import json
import os
import re
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_BIN_DIR = "Scripts" if sys.platform == "win32" else "bin"
NEXUSCLI_ENTRY = REPO_ROOT / ".venv" / _BIN_DIR / "nexuscli"

requires_cli_entry = pytest.mark.skipif(
    not NEXUSCLI_ENTRY.exists(),
    reason=".venv CLI entry point not built (run `uv sync` first)",
)

if sys.platform != "win32":
    # Platform-guarded imports: fcntl/termios/pty do not exist on Windows, and
    # these names are only touched by the pty tests below (skipped there).
    import fcntl
    import pty
    import struct
    import termios

PTY_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="pty is POSIX-only: openpty/fcntl/termios and the tty ISIG Ctrl+C "
    "delivery this test exercises do not exist on Windows",
)

PROMPT_READY = "Type your message"
INTERRUPT_NOTICE = "Interrupted"

_CPR_QUERY = b"\x1b[6n"
_CPR_REPLY = b"\x1b[1;1R"

# Second 8-char chunk of the offline demo's first announcement — its arrival
# proves one full INITIAL_DELAY pacing sleep already elapsed, so the interrupt
# lands mid-turn with the remaining chunks still queued behind it.
_TURN_IN_FLIGHT_MARKER = "读取输入文件"

# Pragmatic ANSI strip (same as test_skill_propose_pty.py): CSI sequences,
# charset designators, OSC strings. Only used for assertions and marker
# matching; the CPR scanner works on the raw stream because ESC[6n is itself
# an escape sequence.
_ANSI_RE = re.compile(
    rb"\x1b\[[0-9;?]*[A-Za-z]"
    rb"|\x1b[()][0-9A-B]"
    rb"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"
)


def _plain(raw: bytes) -> str:
    """ANSI-stripped, whitespace-normalized pty transcript."""
    text = _ANSI_RE.sub(b"", raw).decode("utf-8", "replace")
    return " ".join(text.split())


class _ReplPty:
    """The real ``nexuscli`` REPL subprocess on a self-made, CPR-answered pty."""

    def __init__(self, workdir: Path, home: Path) -> None:
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
            # Scripted offline provider: deterministic, no network. The delay
            # paces each 8-char text chunk so the turn streams for seconds.
            "NEXUSCLI_OFFLINE_DEMO": "1",
            "NEXUSCLI_OFFLINE_DEMO_INITIAL_DELAY": "3.0",
        }
        self.proc = subprocess.Popen(
            [str(NEXUSCLI_ENTRY)],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=str(workdir),
            env=env,
            preexec_fn=_child_on_pty(slave),
            close_fds=True,
        )
        os.close(slave)
        self.master = master
        self.buf = b""
        self._plain = ""
        self._matched_up_to = 0
        self.cpr_answered = 0

    def read_until(self, needle: str, timeout: float) -> None:
        """Read the pty until ``needle`` appears in *new* ANSI-stripped output.

        Answers every ``ESC[6n`` seen so far with ``ESC[1;1R`` — per AGENTS.md
        an unanswered CPR leaves prompt_toolkit mis-positioned and turns any
        later "pass" into a false conclusion. Matching runs against output
        past the previous match only, so waiting for the same marker twice
        (prompt armed, interrupted, re-armed) matches the re-render.
        """
        deadline = time.monotonic() + timeout
        while needle not in self._plain[self._matched_up_to :]:
            if time.monotonic() > deadline:
                raise AssertionError(
                    f"pty output never showed {needle!r} within {timeout}s; "
                    f"cpr answered={self.cpr_answered}; tail:\n{self._plain[-2000:]}"
                )
            if self.proc.poll() is not None:
                raise AssertionError(
                    f"REPL exited rc={self.proc.returncode} while waiting for {needle!r}; "
                    f"tail:\n{self._plain[-2000:]}"
                )
            ready, _, _ = select.select([self.master], [], [], 0.1)
            if not ready:
                continue
            try:
                chunk = os.read(self.master, 65536)
            except OSError:
                break
            if not chunk:
                break
            self.buf += chunk
            self._answer_cpr()
            self._plain = _plain(self.buf)
        pos = self._plain.index(needle, self._matched_up_to)
        self._matched_up_to = pos + len(needle)

    def _answer_cpr(self) -> None:
        for _ in range(self.buf.count(_CPR_QUERY) - self.cpr_answered):
            os.write(self.master, _CPR_REPLY)
            self.cpr_answered += 1

    def send(self, data: bytes) -> None:
        os.write(self.master, data)

    def plain(self) -> str:
        return self._plain

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=10)
        os.close(self.master)


def _child_on_pty(slave_fd: int):
    """Child-side setup: new session + adopt the pty as controlling terminal.

    A session leader must claim the tty explicitly (``TIOCSCTTY``); an
    inherited slave fd does not become the controlling terminal on its own,
    and without it the line discipline delivers ISIG's SIGINT nowhere.
    """

    def setup() -> None:  # pragma: no cover - runs in the forked child
        os.setsid()
        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)

    return setup


# ---------------------------------------------------------------------------
# Mid-turn SIGINT: abort the turn, keep the process, exit 0 on /exit
# ---------------------------------------------------------------------------


@PTY_ONLY
@requires_cli_entry
def test_sigint_during_turn_aborts_turn_and_exits_zero(tmp_path: Path) -> None:
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir()
    work.mkdir()
    session = _ReplPty(work, home)
    try:
        session.read_until(PROMPT_READY, timeout=45)
        assert session.cpr_answered >= 1, (
            "prompt_toolkit never issued ESC[6n; the CPR-answering path this "
            "test exists for was not exercised"
        )
        session.send(b"start\r")
        session.read_until(_TURN_IN_FLIGHT_MARKER, timeout=45)
        time.sleep(0.3)
        # Ctrl+C through the line discipline: a real SIGINT mid-turn.
        session.send(b"\x03")

        # The turn aborts with the user-visible notice — the process survives.
        session.read_until(INTERRUPT_NOTICE, timeout=20)
        assert session.proc.poll() is None, (
            "SIGINT mid-turn killed the REPL process (SIG_DFL regression); "
            f"tail:\n{session.plain()[-1500:]}"
        )

        # The prompt re-arms and a clean /exit ends the process with 0.
        session.read_until(PROMPT_READY, timeout=30)
        session.send(b"/exit\r")
        returncode = session.proc.wait(timeout=25)
        assert returncode == 0
        assert returncode != 130, "exit 130 is the SystemExit(130) misreport this contract forbids"
        transcript = session.plain()
        assert "SystemExit" not in transcript
        assert "Traceback" not in transcript
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Empty-prompt SIGINT: prompt_toolkit's Ctrl+C must still exit 0
# ---------------------------------------------------------------------------


@PTY_ONLY
@requires_cli_entry
def test_sigint_on_empty_prompt_exits_zero_with_cpr_answered(tmp_path: Path) -> None:
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir()
    work.mkdir()
    session = _ReplPty(work, home)
    try:
        session.read_until(PROMPT_READY, timeout=45)
        assert session.cpr_answered >= 1
        session.send(b"\x03")
        returncode = session.proc.wait(timeout=25)
        assert returncode == 0
        assert returncode != 130
        assert "SystemExit" not in session.plain()
        assert "Traceback" not in session.plain()
    finally:
        session.close()
