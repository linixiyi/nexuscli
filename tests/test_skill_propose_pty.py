"""Real-REPL pty review for ``/skill propose`` (spec 2026-11-02 task_0001).

The existing ``test_skill_propose.py`` covers the review loop with stubbed
``Prompt.ask`` answers and a non-TTY stdin gate; here the same flow is driven
through a **self-made pty** against the real in-process REPL
(``start_repl``), so the human-review path runs the way a terminal does:
prompt_toolkit renders and reads the REPL prompt, rich's ``Prompt.ask`` reads
the y / n / s answers from the tty, and the fail-closed non-TTY gate is passed
by an actual tty.

Hermetic by construction:

* the LLM is an in-process scripted fake injected through the minimal command
  seam: ``nexuscli.entrypoints.repl.create_llm_client`` (repl.py imports it at
  module top and calls it in ``start_repl``) — the fake becomes the Agent's
  ``llm_client``, which ``skill.py`` passes to ``ProposalGenerator``. No client
  is ever built from config; as defense-in-depth the redirected HOME's
  config.json pins ``base_url`` to a closed local port (127.0.0.1:9), so even a
  hypothetical real client fails instantly without egress.
* HOME/USERPROFILE are redirected (conftest autouse fixture, plus a
  ``Path.home`` patch here so the REPL thread sees it too); the user-level
  skill and the eval report are fabricated under tmp_path.
* no pexpect: raw ``pty.openpty`` + ``select`` + CPR answering per AGENTS.md.

CPR observation (2026-11-02, macOS arm64, prompt_toolkit 3.x, TERM=xterm-256color):
prompt_toolkit **does** emit ``ESC[6n`` before rendering each REPL prompt on
this pty; the driver answers every query with ``ESC[1;1R``. The count is
recorded on the run but deliberately **not asserted** (spec §2.3: environments
differ — with ``TERM=dumb`` prompt_toolkit skips CPR entirely via
``Vt100_Output.responds_to_cpr``, which is exactly the sandbox shell's default;
the test therefore pins TERM and keeps the answering logic in place).

Output matching is deliberately loose: the pty stream is full of Rich style
codes (which even split styled words — ``item(s)`` arrives as ``item`` + SGR +
``(s)``), so step markers are chosen from plain-text spans and assertions run
against an ANSI-stripped, whitespace-normalized copy. The driver has a total
deadline (select-poll based) and the REPL thread is joined with a timeout, so
a wedged flow fails instead of hanging the suite.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import select
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

import pytest

import nexuscli.entrypoints.repl as repl_module
from nexuscli.config import load_config
from nexuscli.skill.propose import list_proposal_records

if sys.platform != "win32":
    # Platform-guarded imports: fcntl/termios do not exist on Windows, and
    # these names are only touched by the pty tests below (skipped there).
    import fcntl
    import pty
    import struct
    import termios

PTY_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="pty is POSIX-only")

_DRIVER_DEADLINE_SECONDS = 45.0
_JOIN_TIMEOUT_SECONDS = 15.0
_CPR_QUERY = b"\x1b[6n"
_CPR_REPLY = b"\x1b[1;1R"

# The proposal reply the scripted fake returns: two evidence-backed rules, both
# citing the report's failing scenario (the evidence gate keeps both).
RULE_ONE = "Always call write_file to persist the todo list before finishing"
RULE_TWO = "Quote the failing scenario name in the final summary heading"
_SKILL_BODY_MARKER = "unique-skill-body-marker"
_PROPOSAL_REPLY = json.dumps(
    [
        {
            "rule": RULE_ONE,
            "evidence": "scenario todo_flow: expected tool write_file was never called",
        },
        {
            "rule": RULE_TWO,
            "evidence": "scenario todo_flow: expected tool write_file was never called",
        },
    ],
    ensure_ascii=False,
)

SKILL_TEXT = "---\nname: demo\ndescription: demo desc\n---\n" + _SKILL_BODY_MARKER + "\n"


# ---------------------------------------------------------------------------
# pty wrappers: sys.stdin / sys.stdout / sys.stderr backed by the pty slave fd
# ---------------------------------------------------------------------------


class _PtyStdin:
    """sys.stdin stand-in: real fd for prompt_toolkit, readline for rich.

    Under pytest the C-level stdin is not the pty, so builtin ``input()`` (used
    by rich's ``Prompt.ask``) takes its documented fallback and calls
    ``sys.stdin.readline()`` — which must consume exactly one line from the
    slave fd and keep any remainder buffered (prompt_toolkit reads the same fd
    raw between review prompts, so nothing may be swallowed here).
    """

    encoding = "utf-8"

    def __init__(self, fd: int) -> None:
        self._fd = fd
        self._buf = b""

    def fileno(self) -> int:
        return self._fd

    def isatty(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def readline(self) -> str:
        while b"\n" not in self._buf:
            chunk = os.read(self._fd, 1024)
            if not chunk:
                raise EOFError
            self._buf += chunk
        line, _, rest = self._buf.partition(b"\n")
        self._buf = rest
        return line.decode("utf-8", "replace") + "\n"

    def read(self, size: int = -1) -> str:
        data, self._buf = self._buf, b""
        return data.decode("utf-8", "replace")

    def close(self) -> None:
        pass


class _PtyStdout:
    """sys.stdout/stderr stand-in: unbuffered text writes to the slave fd."""

    encoding = "utf-8"

    def __init__(self, fd: int) -> None:
        self._fd = fd

    def fileno(self) -> int:
        return self._fd

    def isatty(self) -> bool:
        return True

    def writable(self) -> bool:
        return True

    def write(self, data: str) -> int:
        return os.write(self._fd, data.encode("utf-8"))

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Scripted in-process LLM (the injection seam's payload)
# ---------------------------------------------------------------------------


class _ScriptedProposeClient:
    """Single-call fake with the LlmClient surface the REPL/Agent touch."""

    model_name = "scripted-propose-fake"
    provider_name = "scripted-propose-fake"
    max_context_window = 128_000
    price_profile = None

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[list[str]] = []

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        self.calls.append([message.content for message in messages])
        yield {"type": "text_delta", "text": self.reply}
        yield {"type": "message_end", "stop_reason": "end_turn"}


# ---------------------------------------------------------------------------
# Output matching helpers
# ---------------------------------------------------------------------------

# Pragmatic ANSI strip: CSI sequences (incl. private modes like ?25l), charset
# designators, OSC strings. Used only for assertions; the driver's CPR scanner
# works on the raw stream because ESC[6n is itself an escape sequence.
_ANSI_RE = re.compile(
    rb"\x1b\[[0-9;?]*[A-Za-z]"
    rb"|\x1b[()][0-9A-B]"
    rb"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"
)


def _plain(raw: bytes) -> str:
    """ANSI-stripped, whitespace-normalized pty transcript (loose matching)."""
    text = _ANSI_RE.sub(b"", raw).decode("utf-8", "replace")
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# The pty driver
# ---------------------------------------------------------------------------


class _PtyDriver:
    """select()-based pty reader: answers CPR, fires scripted keystrokes.

    Steps are ``(marker, keys)`` pairs consumed strictly in order; each step's
    marker must appear in the accumulated output *after* the previous step's
    match. ``keys`` are written to the master when the marker shows up, which
    queues them in the tty line discipline for whoever reads next (the REPL
    prompt or the review prompt) — no keystroke races. The driver never prints
    to stdout: it runs while sys.stdout is the pty, and echoed driver chatter
    could contain later markers and trigger false matches.
    """

    def __init__(
        self, master: int, steps: list[tuple[str, bytes | None]], repl_thread: threading.Thread
    ) -> None:
        self._master = master
        self._steps = steps
        self._repl_thread = repl_thread
        self.buf = b""
        self.events: list[str] = []
        self.cpr_answered = 0

    def run(self) -> None:
        """Drive until all steps fired; raise AssertionError on stall/death."""
        deadline = time.monotonic() + _DRIVER_DEADLINE_SECONDS
        search_from = 0
        step_index = 0
        while step_index < len(self._steps):
            if time.monotonic() > deadline:
                raise AssertionError(self._stalled(f"timeout after {_DRIVER_DEADLINE_SECONDS}s"))
            if not self._repl_thread.is_alive():
                raise AssertionError(self._stalled("REPL thread exited before all steps fired"))
            # CPR: answer every ESC[6n seen so far exactly once.
            for _ in range(self.buf.count(_CPR_QUERY) - self.cpr_answered):
                os.write(self._master, _CPR_REPLY)
                self.cpr_answered += 1
                self.events.append(f"cpr answered #{self.cpr_answered}")
            # Steps: fire the next ones whose marker already arrived.
            while step_index < len(self._steps):
                marker, keys = self._steps[step_index]
                pos = self.buf.find(marker.encode(), search_from)
                if pos == -1:
                    break
                self.events.append(f"step {step_index} matched {marker!r}")
                search_from = pos + len(marker)
                if keys:
                    os.write(self._master, keys)
                step_index += 1
            ready, _, _ = select.select([self._master], [], [], 0.1)
            if not ready:
                continue
            try:
                chunk = os.read(self._master, 65536)
            except OSError:
                raise AssertionError(self._stalled("pty read failed (EIO)")) from None
            if not chunk:
                raise AssertionError(self._stalled("pty reached EOF"))
            self.buf += chunk

    def _stalled(self, reason: str) -> str:
        return (
            f"pty driver stalled: {reason}; events={self.events}; "
            f"cpr answered={self.cpr_answered}; output tail:\n{_plain(self.buf)[-2000:]}"
        )


def _unstick_with_eof(master: int) -> None:
    """Best-effort escape after a driver failure: EOF unwinds whichever reader
    is blocked (prompt_toolkit raises EOFError at the prompt; rich's review
    prompt maps EOF to skip-rest via ``_ask_review``), letting the thread exit."""
    for _ in range(4):
        try:
            os.write(master, b"\x04")
        except OSError:
            return
        time.sleep(0.3)


# ---------------------------------------------------------------------------
# Full flow: boot the real REPL in-process on a pty and drive /skill propose
# ---------------------------------------------------------------------------


def _write_user_skill(home: Path) -> Path:
    skill_dir = home / ".nexuscli" / "skills" / "demo"
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text(SKILL_TEXT, encoding="utf-8")
    return path


def _write_hermetic_report(work: Path) -> Path:
    path = work / ".nexuscli" / "evals" / "last-report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "generated_at": "2026-11-02T10:00:00",
                "scenarios": [
                    {
                        "name": "todo_flow",
                        "status": "fail",
                        "failure_reasons": ["expected tool write_file was never called"],
                        "cost": {"change": "unchanged", "warnings": []},
                    },
                    {
                        "name": "search_flow",
                        "status": "pass",
                        "failure_reasons": [],
                        "cost": {"change": "increased", "warnings": ["4 turns → 7 turns"]},
                    },
                ],
                "summary": {"total": 2, "passed": 1, "failed": 1},
            }
        ),
        encoding="utf-8",
    )
    return path


def _history_root(home: Path) -> Path:
    return home / ".nexuscli" / "skill_history"


def _audit_lines(home: Path) -> list[dict[str, Any]]:
    path = _history_root(home) / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class _ReplRun:
    """Everything the assertions need after one driven REPL session."""

    def __init__(
        self,
        *,
        home: Path,
        plain_output: str,
        cpr_answered: int,
        llm_calls: list[list[str]],
    ) -> None:
        self.home = home
        self.plain_output = plain_output
        self.cpr_answered = cpr_answered
        self.llm_calls = llm_calls


def _run_propose_in_repl(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, second_answer: str
) -> _ReplRun:
    """Boot the real REPL in-process on a self-made pty and drive one
    ``/skill propose demo`` session whose two review answers are y and
    ``second_answer``; return the collected transcript and artifacts."""

    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir()
    work.mkdir()
    # HOME/USERPROFILE win over the conftest autouse fixture (it documents this
    # ordering); Path.home is patched too so the REPL thread follows.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    # The sandbox shell may export TERM=dumb, which makes prompt_toolkit skip
    # CPR and degrade rendering; pin a capable terminal like the subprocess
    # pty tests do, and fix rich's wrap width deterministically.
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("LINES", "40")
    # Belt-and-braces hermeticity: no demo provider, and the (never-built)
    # config-chain client would point at a closed local port.
    monkeypatch.delenv("NEXUSCLI_OFFLINE_DEMO", raising=False)
    (home / ".nexuscli").mkdir()
    (home / ".nexuscli" / "config.json").write_text(
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

    _write_user_skill(home)
    _write_hermetic_report(work)

    fake_client = _ScriptedProposeClient(_PROPOSAL_REPLY)
    # The minimal seam: repl.py imports create_llm_client at module top and
    # calls it inside start_repl; the returned client becomes Agent.llm_client,
    # which _skill_propose passes to ProposalGenerator (skill.py:244).
    monkeypatch.setattr(repl_module, "create_llm_client", lambda *args, **kwargs: fake_client)

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 200, 0, 0))

    outcome: dict[str, str] = {}

    def _repl_main() -> None:
        saved = (sys.stdin, sys.stdout, sys.stderr)
        sys.stdin = _PtyStdin(slave)
        sys.stdout = _PtyStdout(slave)
        sys.stderr = _PtyStdout(slave)
        try:
            asyncio.run(repl_module.start_repl(str(work), load_config(project_root=str(work))))
            outcome["done"] = "repl returned"
        except BaseException as exc:  # noqa: BLE001 - surfaced to the test
            outcome["error"] = "".join(traceback.format_exception(exc)).strip()
        finally:
            sys.stdin, sys.stdout, sys.stderr = saved

    repl_thread = threading.Thread(target=_repl_main, name="repl-under-test", daemon=True)
    repl_thread.start()

    steps: list[tuple[str, bytes | None]] = [
        # 1) first REPL prompt armed → type the command (raw mode: CR is Enter)
        ("Type your message", b"/skill propose demo\r"),
        # 2) the proposal header printed → the fake LLM answered and the review
        #    loop started; no keys, just advance the marker cursor
        ("proposal", None),
        # 3)+4) one review prompt per item → answer y, then y / s
        ("y / n / s", b"y\r"),
        ("y / n / s", second_answer.encode() + b"\r"),
        # 5) the re-armed REPL prompt after the summary → leave cleanly
        ("Type your message", b"/exit\r"),
    ]
    driver = _PtyDriver(master, steps, repl_thread)
    failure: str | None = None
    try:
        try:
            driver.run()
        except AssertionError as exc:
            failure = str(exc)
            _unstick_with_eof(master)
    finally:
        repl_thread.join(_JOIN_TIMEOUT_SECONDS)
        thread_alive = repl_thread.is_alive()
        repl_error = outcome.get("error")
        os.close(master)
        os.close(slave)

    assert failure is None, failure
    assert not thread_alive, (
        f"REPL thread still alive after /exit (driver events: {driver.events})\n"
        f"{_plain(driver.buf)[-1500:]}"
    )
    assert repl_error is None, f"REPL thread crashed:\n{repl_error}\n{_plain(driver.buf)[-1500:]}"

    return _ReplRun(
        home=home,
        plain_output=_plain(driver.buf),
        cpr_answered=driver.cpr_answered,
        llm_calls=fake_client.calls,
    )


# ---------------------------------------------------------------------------
# Path 1: y, y — both rules land, audit + record reflect it
# ---------------------------------------------------------------------------


@PTY_ONLY
def test_pty_review_y_y_lands_both_rules_with_audit_and_record(tmp_path, monkeypatch):
    run = _run_propose_in_repl(monkeypatch, tmp_path, second_answer="y")
    home = run.home

    # The scripted in-process fake served exactly one propose call carrying the
    # skill text and the report digest — proof of the injection seam (and that
    # no config-chain/real client was ever consulted).
    assert len(run.llm_calls) == 1
    user_message = run.llm_calls[0][0]
    assert _SKILL_BODY_MARKER in user_message
    assert "todo_flow" in user_message

    # Loose transcript assertions (ANSI-stripped): header, per-item reason
    # lines, per-item landing confirmations.
    assert 'skill propose: 2 proposal item(s) for "demo"' in run.plain_output
    assert (
        "Reason: scenario todo_flow: expected tool write_file was never called" in run.plain_output
    )
    assert f"1. {RULE_ONE}" in run.plain_output
    assert f"2. {RULE_TWO}" in run.plain_output
    assert "Applied; pre-change state kept as v1." in run.plain_output
    assert "Applied; pre-change state kept as v2." in run.plain_output

    # SKILL.md: both rules appended under ## Evolved Rules, frontmatter intact.
    skill_path = home / ".nexuscli" / "skills" / "demo" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert "## Evolved Rules" in content
    assert RULE_ONE in content
    assert RULE_TWO in content
    assert "(reason: propose)" in content

    # Pre-change snapshots frozen by the evolution store.
    history = _history_root(home) / "demo"
    assert (history / "v1.md").read_text(encoding="utf-8") == SKILL_TEXT
    assert (history / "v2.md").exists()

    # Audit: one op=save line per landing, reason=propose, versions 1 then 2.
    audit = _audit_lines(home)
    assert [(row["op"], row["reason"], row["version"]) for row in audit] == [
        ("save", "propose", 1),
        ("save", "propose", 2),
    ]

    # Proposal record: both items applied with their landing versions.
    records = list_proposal_records(_history_root(home), "demo")
    assert len(records) == 1
    record = records[0][1]
    assert [item["decision"] for item in record["items"]] == ["applied", "applied"]
    assert [item["version"] for item in record["items"]] == [1, 2]
    assert record["applied_versions"] == [1, 2]
    assert record["interactive"] is True
    assert record["track"] == "hermetic"
    assert record["report_path"].endswith("last-report.json")


# ---------------------------------------------------------------------------
# Path 2: y, s — first rule lands, second skipped, audit has one line
# ---------------------------------------------------------------------------


@PTY_ONLY
def test_pty_review_y_s_lands_first_and_skips_rest(tmp_path, monkeypatch):
    run = _run_propose_in_repl(monkeypatch, tmp_path, second_answer="s")
    home = run.home

    assert len(run.llm_calls) == 1

    # Transcript: item 1 applied, item 2's answer stopped the loop.
    assert "Reason: scenario todo_flow" in run.plain_output
    assert "Applied; pre-change state kept as v1." in run.plain_output
    assert "Skipping the remaining items" in run.plain_output
    assert "Applied; pre-change state kept as v2." not in run.plain_output

    # SKILL.md: only the first rule landed.
    skill_path = home / ".nexuscli" / "skills" / "demo" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert "## Evolved Rules" in content
    assert RULE_ONE in content
    assert RULE_TWO not in content

    # Audit: exactly one save line (the second item never landed).
    audit = _audit_lines(home)
    assert [(row["op"], row["reason"], row["version"]) for row in audit] == [("save", "propose", 1)]
    assert (_history_root(home) / "demo" / "v1.md").read_text(encoding="utf-8") == SKILL_TEXT
    assert not (_history_root(home) / "demo" / "v2.md").exists()

    # Proposal record: applied + skipped, the skip carrying no version.
    records = list_proposal_records(_history_root(home), "demo")
    assert len(records) == 1
    record = records[0][1]
    assert [item["decision"] for item in record["items"]] == ["applied", "skipped"]
    assert record["items"][0]["version"] == 1
    assert record["items"][1]["version"] is None
    assert record["applied_versions"] == [1]
    assert record["interactive"] is True
