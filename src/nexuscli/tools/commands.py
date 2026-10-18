from __future__ import annotations

import asyncio
import contextlib
import os
import re
import signal
import time
from dataclasses import dataclass

from nexuscli.policy import CommandGuard

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

MAX_OUTPUT_CHARS = 20_000
TRUNCATION_SUFFIX = "\n... [output truncated]"
STDERR_OMITTED_SUFFIX = "\n... [stderr omitted: output limit spent on stdout]"


@dataclass(slots=True)
class CommandResult:
    """Structured result of a command execution."""

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    truncated: bool = False

    @property
    def combined(self) -> str:
        return self.stdout + self.stderr

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def summary(self) -> str:
        if self.timed_out:
            return "(timed out)"
        return f"(exit {self.exit_code}, no output)" if not self.combined.strip() else ""


# ---------------------------------------------------------------------------
# Sensitive-command heuristics for HITL prompts
# ---------------------------------------------------------------------------

# Patterns that indicate a command modifies the system or has side effects
_WRITE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(rm|mv|cp|chmod|chown|ln)\b"),
    re.compile(r"\b(mkdir|touch|truncate)\b"),
    re.compile(r"\b(wget|curl)\s+.*[-]O\b"),
    re.compile(r"\b(git\s+push|git\s+commit|git\s+reset|git\s+rebase|git\s+merge)\b"),
    re.compile(
        r"\b(pip|npm|brew|apt|yum|dnf|pacman)\s+(install|uninstall|remove|update|upgrade)\b"
    ),
    re.compile(r"\b(kubectl|helm|docker|podman)\s+(apply|create|delete|run|exec|port-forward)\b"),
    re.compile(r"\b(>|>>)\s*[^\s]"),
]

_DESTRUCTIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\s+-[rf]+\s"),
    re.compile(r"\bdd\s+if="),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bshutdown\b"),
    re.compile(r"\breboot\b"),
    re.compile(r"\bchmod\s+-R\s+777\s+/"),
    re.compile(r":\(\)\s*\{"),
    re.compile(r"\bfind\s+/\s"),
    # Piping into an interpreter is download-and-execute — treat as destructive.
    re.compile(r"\|.*\b(sh|bash|zsh|ksh)\b"),
]


def classify_command(command: str) -> str:
    """Classify a command into a danger level string for HITL display."""
    norm = command.strip()
    if not norm:
        return "safe"

    for pattern in _DESTRUCTIVE_PATTERNS:
        if pattern.search(norm):
            return "high"

    for pattern in _WRITE_PATTERNS:
        if pattern.search(norm):
            return "medium"

    return "safe"


# ---------------------------------------------------------------------------
# Process groups
# ---------------------------------------------------------------------------

# A killed command sits on the caller's critical path, so the wait below is a
# poll: SIGTERM is honoured immediately by well-behaved children (sleep, sh),
# and polling returns the moment the group empties instead of sleeping out the
# whole grace period. The full grace is only paid when a child ignores SIGTERM
# — exactly the case where the escalation is needed anyway.
_KILL_GRACE_SECONDS = 2.0
_GROUP_POLL_SECONDS = 0.05


def process_group_spawn_kwargs() -> dict[str, bool]:
    """Subprocess keyword arguments that give the child its own process group.

    POSIX: ``start_new_session`` runs setsid(), so the child leads a fresh
    session and one killpg reaches everything it forked. Without it a
    ``sh -c '... & ...'`` grandchild stays in *our* group — terminating the
    shell leaves the grandchild running, and signalling our own group would
    take the CLI down with it.

    Windows: ``start_new_session`` is documented POSIX-only and CPython
    ignores it there, so passing it would advertise a group that does not
    exist. Windows therefore gets no group at spawn, and termination degrades
    to terminate/kill on the direct child — see :func:`kill_process_group`.
    """
    if os.name != "posix":
        return {}
    return {"start_new_session": True}


def process_group_of(pid: int | None) -> int | None:
    """Return the group id of *pid*, or None when the platform or pid cannot tell."""
    if pid is None or os.name != "posix":
        return None
    try:
        return os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        return None


def process_group_alive(pgid: int | None) -> bool:
    """True while any member of *pgid* still exists (signal-0 probe)."""
    if pgid is None or os.name != "posix":
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Present but not ours to signal: report it alive rather than claim a
        # kill that did not happen.
        return True
    return True


def signal_process_group(pgid: int | None, *, hard: bool = False) -> None:
    """Signal every member of *pgid*: SIGTERM by default, SIGKILL when *hard*."""
    if pgid is None or os.name != "posix":
        return
    # An empty group is the expected outcome when the leader exited first.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pgid, signal.SIGKILL if hard else signal.SIGTERM)


async def kill_process_group(
    pgid: int | None,
    process: object,
    grace: float = _KILL_GRACE_SECONDS,
) -> bool:
    """Terminate *process* and everything it forked; True when the group is gone.

    False is returned only when members outlived SIGKILL, and callers must
    report that honestly instead of rendering it as a completed stop.
    """
    if pgid is None:
        return await _stop_direct_child(process, grace)
    signal_process_group(pgid)
    gone = await _wait_group_gone(pgid, grace)
    if not gone:
        signal_process_group(pgid, hard=True)
        gone = await _wait_group_gone(pgid, grace)
    # Reap even when the group survived: a stale returncode would make the
    # leader look alive and hide that the stop did reach it.
    await _reap(process, grace)
    return gone


async def _stop_direct_child(process: object, grace: float) -> bool:
    """Degraded ladder for platforms without process groups (Windows).

    ``terminate`` on the leader is all that is available: a ``cmd /c`` shell's
    grandchildren outlive it. That boundary is deliberate — the alternative
    is a platform-specific tree walk whose failure modes cost more than the
    leaked process.
    """
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=grace)
    except TimeoutError:
        process.kill()
        await process.wait()
    return True


async def _wait_group_gone(pgid: int, grace: float) -> bool:
    deadline = time.monotonic() + grace
    while process_group_alive(pgid):
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(_GROUP_POLL_SECONDS)
    return True


async def _reap(process: object, grace: float) -> None:
    """Collect the group leader's exit status; bounded so a stop never hangs."""
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=grace)


async def _drain_reader(
    reader: asyncio.Future[tuple[bytes, bytes]],
    grace: float,
) -> tuple[bytes, bytes]:
    """Collect what the pipes still hold after the group was killed.

    Bounded by *grace*: a writer that escaped the group (it called setsid
    itself) would otherwise hold the pipe open forever and reintroduce the
    very hang the group kill removed.
    """
    try:
        stdout, stderr = await asyncio.wait_for(reader, timeout=grace)
    except TimeoutError:
        reader.cancel()
        return b"", b""
    return stdout or b"", stderr or b""


async def communicate_or_kill_group(
    process: asyncio.subprocess.Process,
    timeout: float,
    grace: float = _KILL_GRACE_SECONDS,
) -> tuple[bytes, bytes, bool]:
    """Await *process* output, killing its whole group when *timeout* expires.

    Returns ``(stdout, stderr, timed_out)``. A bare ``communicate()`` is not
    enough: it only returns once both pipes hit EOF, and a grandchild that
    inherited them keeps them open after the shell is gone — the call would
    hang for as long as the grandchild lives. Killing the group first makes
    the pipes reach EOF, so the drain returns right after the timeout.
    """
    reader = asyncio.ensure_future(process.communicate())
    try:
        stdout, stderr = await asyncio.wait_for(asyncio.shield(reader), timeout=timeout)
    except TimeoutError:
        # SIGTERM the group first so children can flush and exit, then
        # escalate; only then does the pipe reach EOF and the drain return.
        await kill_process_group(process_group_of(process.pid), process, grace)
        stdout, stderr = await _drain_reader(reader, grace)
        return stdout, stderr, True
    return stdout or b"", stderr or b"", False


# ---------------------------------------------------------------------------
# CommandExecutor
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CommandExecutionOptions:
    """Fine-grained options for a single command execution."""

    cwd: str | None = None
    timeout: float = 60.0
    env: dict[str, str] | None = None
    max_output_chars: int = MAX_OUTPUT_CHARS
    suppress_shell: bool = False  # When true, use Popen with list-form instead of shell


class CommandExecutor:
    """Encapsulates subprocess execution with security, timeout, and output handling.

    Usage::

        executor = CommandExecutor(command_guard=CommandGuard())
        result = await executor.execute("ls -la", cwd="/workspace")
    """

    def __init__(
        self,
        command_guard: CommandGuard | None = None,
        default_timeout: float = 60.0,
        default_max_output: int = MAX_OUTPUT_CHARS,
    ) -> None:
        self.command_guard = command_guard or CommandGuard()
        self.default_timeout = default_timeout
        self.default_max_output = default_max_output

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
        max_output_chars: int | None = None,
    ) -> CommandResult:
        """Execute *command* via a shell subprocess.

        Parameters
        ----------
        command:
            Shell command string.
        cwd:
            Working directory (default: CWD of current process).
        timeout:
            Maximum seconds the command is allowed to run.
        env:
            Extra or override environment variables.
        max_output_chars:
            Maximum characters kept from combined stdout+stderr.

        Raises
        ------
        CommandPolicyError
            If the command is blocked by the command guard policy.
        """
        self._validate(command)

        resolved_cwd = cwd or os.getcwd()
        resolved_timeout = timeout if timeout is not None else self.default_timeout
        resolved_max_output = (
            max_output_chars if max_output_chars is not None else self.default_max_output
        )

        merged_env = self._build_env(env)

        return await self._run_subprocess(
            command=command,
            cwd=resolved_cwd,
            timeout=resolved_timeout,
            env=merged_env,
            max_output_chars=resolved_max_output,
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self, command: str) -> None:
        if self.command_guard:
            self.command_guard.validate(command)

    # ------------------------------------------------------------------
    # Environment
    # ------------------------------------------------------------------

    @staticmethod
    def _build_env(overrides: dict[str, str] | None) -> dict[str, str]:
        env = os.environ.copy()
        if overrides:
            env.update(overrides)
        return env

    # ------------------------------------------------------------------
    # Subprocess runners
    # ------------------------------------------------------------------

    async def _run_subprocess(
        self,
        command: str,
        cwd: str,
        timeout: float,
        env: dict[str, str],
        max_output_chars: int,
    ) -> CommandResult:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            **process_group_spawn_kwargs(),
        )
        return await self._handle_process(proc, command, timeout, max_output_chars)

    async def _handle_process(
        self,
        proc: asyncio.subprocess.Process,
        command: str,
        timeout: float,
        max_output_chars: int,
    ) -> CommandResult:
        stdout, stderr, timed_out = await communicate_or_kill_group(proc, timeout)

        stdout_str = _decode(stdout)
        stderr_str = _decode(stderr)

        truncated = False
        combined_len = len(stdout_str) + len(stderr_str)
        if combined_len > max_output_chars:
            truncated = True
            # Prefer to keep stdout over stderr; keep a marker so stderr loss is visible
            avail = max_output_chars - len(TRUNCATION_SUFFIX)
            if len(stdout_str) >= avail:
                stdout_str = stdout_str[:avail] + TRUNCATION_SUFFIX
                stderr_str = STDERR_OMITTED_SUFFIX
            else:
                stderr_str = stderr_str[: avail - len(stdout_str)] + TRUNCATION_SUFFIX

        return CommandResult(
            stdout=stdout_str,
            stderr=stderr_str,
            exit_code=-1 if timed_out else (proc.returncode or 0),
            timed_out=timed_out,
            truncated=truncated,
        )


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _decode(data: bytes) -> str:
    """Decode bytes with UTF-8 fallback."""
    return data.decode("utf-8", errors="replace")
