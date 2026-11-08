"""os_sandbox.py — OS-level command sandboxing (SandboxBackend protocol).

Where :mod:`nexuscli.policy.sandbox` sanitizes the *environment* a child
process sees, this module confines what the child can *do*: OS-level
sandboxing of spawned shell commands. A ``SandboxBackend`` wraps a shell
command string so the child runs under an OS confinement mechanism — on
macOS the Seatbelt policy engine (``sandbox-exec``); on Linux the
bubblewrap and Landlock backends (:mod:`nexuscli.policy.linux_sandbox`)
plug into the same protocol.

Config surface: ``sandbox.backend = "none" | "seatbelt" | "bubblewrap" |
"landlock"`` (default ``"none"`` — behaviour identical to a build without
this module). See :class:`nexuscli.config.SandboxConfig`.

Honest boundaries:

- The seatbelt profile is *minimally usable*, not a hard security boundary.
  File reads and Mach lookups stay open — loading interpreters,
  site-packages and launchd services must keep working — so a hostile child
  can still read everything the user can and talk to XPC services. The
  confinement target is writes (the command's cwd, the system temp tree,
  ``/dev/null`` and explicit extras only) and the network (denied unless
  explicitly allowed; even then inbound/bind stay denied).
- ``sandbox-exec`` is deprecated by Apple yet remains the official macOS
  policy mechanism; a future macOS may drop it. ``available()`` and
  ``probe()`` exist exactly so that degrades into a typed failure instead
  of a broken spawn.
- Fail-closed contract: a backend that is unknown or unusable on this host
  raises :class:`OperationUnsupported`. There is deliberately no fallback
  to "none" — a caller that asked for confinement must never get an
  unconfined spawn without an error.
- Current wiring: :class:`nexuscli.tools.commands.CommandExecutor` accepts
  a ``SandboxConfig`` and wraps at spawn time — after command-guard
  validation, so guards always analyze the caller's original command, never
  the wrapped string. The model-facing bash tool
  (:func:`nexuscli.tools.builtins._bash`) reads the run's
  ``context.config.sandbox`` and wraps through :func:`wrap_command` after
  its guards, covering the background-task and the foreground spawn path
  alike; the default backend ``"none"`` returns the command untouched.
  Callers that build their own executor outside the run's config (the
  ``!``-injection CommandExecutor in slash_commands) must pass the config
  explicitly and do not yet.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

# Names accepted by ``sandbox.backend``. "none" is the unconfined default;
# everything else must resolve to a backend that can actually run here.
SUPPORTED_BACKENDS: tuple[str, ...] = ("none", "seatbelt", "bubblewrap", "landlock")

BACKEND_NONE = "none"
BACKEND_SEATBELT = "seatbelt"
BACKEND_BUBBLEWRAP = "bubblewrap"
BACKEND_LANDLOCK = "landlock"

# Backends implemented in :mod:`nexuscli.policy.linux_sandbox` and resolved
# through a lazy import from :func:`resolve_backend` — this module stays the
# protocol home without importing the Linux implementation eagerly.
LINUX_BACKENDS: tuple[str, ...] = (BACKEND_BUBBLEWRAP, BACKEND_LANDLOCK)

SEATBELT_EXECUTABLE = "sandbox-exec"
# The wrapped command is a shell string, so the sandboxed program is a
# shell: sandbox-exec takes a program argv, not shell syntax. Absolute path
# because PATH inside the sandbox is whatever the caller handed us.
SEATBELT_SHELL = "/bin/sh"

# A hung sandbox-exec probe must not stall a spawn path; the probe runs a
# single builtin-only shell command, so 10s is generous.
_PROBE_TIMEOUT_SECONDS = 10.0
# The probe command is a pure shell builtin: nothing is exec'd, nothing is
# written, no network. If this cannot exit 0 under the profile, nothing can.
_PROBE_COMMAND = "exit 0"

# Runner abstraction for probe(): the default executes argv directly;
# tests inject a fake to assert argv shape without spawning anything.
ProbeRunner = Callable[[list[str]], subprocess.CompletedProcess]


class OperationUnsupported(RuntimeError):
    """The requested sandbox backend cannot run on this host (fail-closed).

    Raised instead of silently downgrading to "none": confinement that was
    requested and cannot be delivered must fail the spawn, not ignore the
    request.
    """


@dataclass(frozen=True, slots=True)
class SandboxRequest:
    """What one wrapped command needs from its confinement."""

    cwd: str
    writable_paths: tuple[str, ...] = ()
    allow_network: bool = False


class SandboxBackend(Protocol):
    """Protocol every OS-sandbox backend implements (probe/wrap/available).

    - ``available()``: cheap static check — platform and tooling present.
      No side effects, safe to call anywhere.
    - ``probe()``: live check — actually confine one trivial command and
      require a clean exit. May spawn a subprocess.
    - ``wrap(command, request)``: pure string work — return *command*
      wrapped for this backend as a shell command string.
    """

    name: str

    def available(self) -> bool: ...

    def probe(self) -> bool: ...

    def wrap(self, command: str, request: SandboxRequest) -> str: ...


# ---------------------------------------------------------------------------
# macOS Seatbelt backend
# ---------------------------------------------------------------------------


def _default_probe_runner(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        capture_output=True,
        check=False,
        timeout=_PROBE_TIMEOUT_SECONDS,
    )


class SeatbeltBackend:
    """macOS Seatbelt backend: wraps commands with ``sandbox-exec -p``.

    *which* / *runner* / *platform* are injection points for tests; the
    defaults resolve the binary on PATH, run the probe subprocess and read
    ``sys.platform``.
    """

    name = BACKEND_SEATBELT

    def __init__(
        self,
        *,
        which: Callable[[str], str | None] = shutil.which,
        runner: ProbeRunner | None = None,
        platform: str | None = None,
    ) -> None:
        self._which = which
        self._runner: ProbeRunner = runner if runner is not None else _default_probe_runner
        self._platform = sys.platform if platform is None else platform

    def available(self) -> bool:
        """True on macOS with ``sandbox-exec`` resolvable on PATH."""
        return self._platform == "darwin" and self._which(SEATBELT_EXECUTABLE) is not None

    def probe(self) -> bool:
        """Live check: run one builtin-only command under the profile."""
        executable = self._which(SEATBELT_EXECUTABLE)
        if executable is None:
            return False
        argv = _sandbox_exec_argv(executable, _PROBE_COMMAND, SandboxRequest(cwd=os.getcwd()))
        try:
            completed = self._runner(argv)
        except (OSError, subprocess.SubprocessError):
            return False
        return completed.returncode == 0

    def wrap(self, command: str, request: SandboxRequest) -> str:
        """Return ``sandbox-exec -p <profile> /bin/sh -c <command>``.

        Pure string work: the profile and the inner command are embedded
        with :func:`shlex.quote`, so the outer shell round-trips both
        verbatim — the profile arrives at sandbox-exec as one argument and
        the inner shell sees the original command unchanged.
        """
        profile = build_seatbelt_profile(request)
        return " ".join(
            (
                shlex.quote(SEATBELT_EXECUTABLE),
                "-p",
                shlex.quote(profile),
                shlex.quote(SEATBELT_SHELL),
                "-c",
                shlex.quote(command),
            )
        )


def _sandbox_exec_argv(executable: str, command: str, request: SandboxRequest) -> list[str]:
    """argv form of one sandboxed run (used by probe, no shell involved)."""
    return [
        executable,
        "-p",
        build_seatbelt_profile(request),
        SEATBELT_SHELL,
        "-c",
        command,
    ]


def build_seatbelt_profile(request: SandboxRequest) -> str:
    """Render the minimal-usable SBPL profile for one wrapped command.

    - ``(deny default)``: everything not explicitly allowed is denied — the
      only safe default for a confinement profile.
    - Reads, process exec/fork, sysctl reads and Mach lookups stay open:
      interpreters, package data and launchd services must keep working
      (see the module docstring for why this is not a hard boundary).
    - Writes are confined to the command's cwd (both spellings when it sits
      under a symlinked prefix), the system temp tree, ``/dev/null`` and any
      explicit ``writable_paths``.
    - Network is denied outright unless ``allow_network`` requests outbound
      access; inbound/bind stay denied either way.
    """
    lines = [
        "(version 1)",
        "(deny default)",
        "; reads and mach-lookup stay open: interpreters and launchd must work",
        "(allow file-read*)",
        "(allow process-exec*)",
        "(allow process-fork)",
        "(allow sysctl-read)",
        "(allow mach-lookup)",
        "; writes: cwd, system temp, /dev/null and explicit extras only",
        "(allow file-write*",
    ]
    lines.extend(f"    (subpath {_sbpl_string(path)})" for path in writable_subpaths(request))
    lines.append(")")
    if request.allow_network:
        lines.extend(
            (
                "; outbound network allowed by config; bind/inbound stay denied",
                "(allow network-outbound)",
                "(allow system-socket)",
            )
        )
    else:
        lines.append("; network denied (sandbox.allow_network is false)")
        lines.append("(deny network*)")
    return "\n".join(lines) + "\n"


def writable_subpaths(request: SandboxRequest) -> list[str]:
    """Dedup'd writable subpath list for one request.

    Shared by every backend (Seatbelt profile, bubblewrap binds, landlock
    rules): the command's cwd, the system temp tree, ``/dev/null`` and any
    explicit ``writable_paths``. Every candidate goes through
    :func:`os.path.realpath` because the confinement mechanisms match the
    path as the kernel resolves it, and macOS spells the temp tree both
    ways (``/tmp`` → ``/private/tmp``); the fixed spellings below exist so
    the list stays stable even where realpath is unavailable.
    """
    candidates: list[str] = [
        "/dev/null",
        "/tmp",
        "/private/tmp",
        tempfile.gettempdir(),
        request.cwd,
        *request.writable_paths,
    ]
    seen: dict[str, None] = {}
    for path in candidates:
        if not path:
            continue
        normalized = os.path.normpath(path)
        if normalized:
            seen.setdefault(normalized, None)
            seen.setdefault(os.path.realpath(normalized), None)
    return list(seen)


def _sbpl_string(path: str) -> str:
    """Quote *path* as an SBPL double-quoted string."""
    escaped = path.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


# ---------------------------------------------------------------------------
# Resolution: config value -> ready backend (fail-closed)
# ---------------------------------------------------------------------------


def resolve_backend(
    name: str,
    *,
    which: Callable[[str], str | None] = shutil.which,
    runner: ProbeRunner | None = None,
    platform: str | None = None,
) -> SandboxBackend | None:
    """Turn a ``sandbox.backend`` config value into a ready backend.

    Returns None for ``"none"`` (no OS confinement configured). Anything
    else must be a known backend that is *available* on this host and whose
    live :meth:`SandboxBackend.probe` passes — otherwise
    :class:`OperationUnsupported` is raised. There is deliberately no
    silent fallback to "none": a requested confinement that cannot be
    delivered must fail the caller, not degrade the spawn.

    ``"seatbelt"`` is resolved here; the Linux backends (``bubblewrap`` /
    ``landlock``) are delegated to
    :func:`nexuscli.policy.linux_sandbox.resolve_linux_backend` through a
    lazy import, which keeps this module's import graph one-directional.

    *which* / *runner* / *platform* are the test injection points, shared
    with :class:`SeatbeltBackend` and the Linux backends.
    """
    if name == BACKEND_NONE:
        return None
    if name in LINUX_BACKENDS:
        from nexuscli.policy.linux_sandbox import resolve_linux_backend

        return resolve_linux_backend(name, which=which, runner=runner, platform=platform)
    if name != BACKEND_SEATBELT:
        raise OperationUnsupported(
            f"unknown sandbox backend {name!r}; supported backends: "
            + ", ".join(SUPPORTED_BACKENDS)
        )
    backend = SeatbeltBackend(which=which, runner=runner, platform=platform)
    if not backend.available():
        raise OperationUnsupported(
            f"sandbox backend {name!r} is not available on this host "
            f"(platform={platform if platform is not None else sys.platform}, "
            f"{SEATBELT_EXECUTABLE} not usable here)"
        )
    if not backend.probe():
        raise OperationUnsupported(
            f"sandbox backend {name!r} probe failed: a trivial command did not "
            "survive confinement on this host"
        )
    return backend


def wrap_command(
    command: str,
    *,
    backend: str,
    cwd: str | None = None,
    writable_paths: Sequence[str] = (),
    allow_network: bool = False,
    which: Callable[[str], str | None] = shutil.which,
    runner: ProbeRunner | None = None,
    platform: str | None = None,
) -> str:
    """Resolve *backend* and wrap *command*; ``"none"`` returns it unchanged.

    The single entry point a spawn site calls. Fail-closed via
    :func:`resolve_backend`: an unknown or unusable backend raises
    :class:`OperationUnsupported` instead of returning the unconfined
    command.
    """
    resolved = resolve_backend(backend, which=which, runner=runner, platform=platform)
    if resolved is None:
        return command
    request = SandboxRequest(
        cwd=cwd if cwd is not None else os.getcwd(),
        writable_paths=tuple(writable_paths),
        allow_network=allow_network,
    )
    return resolved.wrap(command, request)
