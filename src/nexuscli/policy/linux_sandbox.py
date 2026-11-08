"""linux_sandbox.py — Linux backends (bubblewrap / Landlock) of the OS sandbox.

Implements the :class:`~nexuscli.policy.os_sandbox.SandboxBackend` protocol
for Linux so ``sandbox.backend`` means the same thing on both platforms:

- ``"bubblewrap"``: every command runs inside a `bubblewrap`_ (``bwrap``)
  mount namespace — the root filesystem is bound read-only, a fresh
  ``/dev`` and ``/proc`` are mounted, and the only writable spots are the
  command's cwd, the system temp tree, ``/dev/shm`` and explicit
  ``writable_paths``. Network is toggled with ``--unshare-net``.
- ``"landlock"``: commands are wrapped in the small in-tree helper
  (:data:`LANDLOCK_HELPER`) that applies a `Landlock`_ ruleset to itself and
  then execs the command. Writes are confined to the same writable set as
  Seatbelt (``/dev/null``, the temp tree, cwd, extras); reads stay open
  because they are not handled at all. Network denial needs Landlock
  ABI 4 (Linux >= 6.7) and covers TCP (UDP from ABI 10).

Bubblewrap is the preferred backend — :func:`preferred_linux_backend`
encodes the order (bubblewrap first, Landlock fallback) and the typed
errors of both backends point at the other one when it is usable here.

Fail-closed contract, same as the Seatbelt backend: an unavailable or
unprovable backend raises :class:`~nexuscli.policy.os_sandbox.OperationUnsupported`
with install hints. There is deliberately no fallback to ``"none"``.

Honest boundaries:

- bubblewrap needs unprivileged user namespaces (or a setuid ``bwrap``);
  containers routinely disable both, which is exactly what ``probe()``
  catches. Its network toggle is coarser than Seatbelt's: ``--unshare-net``
  kills the whole network namespace including loopback, while "allowed"
  means fully open (bind included).
- Landlock confinement is not a hard security boundary either: the
  environment, SysV/POSIX IPC, signals, TTY ioctls and pathname UNIX-socket
  lookups (``RESOLVE_UNIX``, ABI 9) stay open. On ABI 4–9 kernels UDP is
  not covered by the network toggle. The helper confine *itself* and then
  execs the command — it must therefore run as a child, which is why
  ``probe()`` spawns the helper instead of restricting the caller.
- The landlock helper runs under the current interpreter
  (``sys.executable``); if that interpreter is missing at spawn time the
  wrap fails visibly, never silently.

.. _bubblewrap: https://github.com/containers/bubblewrap
.. _Landlock: https://docs.kernel.org/userspace-api/landlock.html
"""

from __future__ import annotations

import ctypes
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable

from nexuscli.policy.os_sandbox import (
    BACKEND_BUBBLEWRAP,
    BACKEND_LANDLOCK,
    LINUX_BACKENDS,
    SUPPORTED_BACKENDS,
    OperationUnsupported,
    ProbeRunner,
    SandboxBackend,
    SandboxRequest,
    writable_subpaths,
)

BUBBLEWRAP_EXECUTABLE = "bwrap"
# The wrapped command is a shell string, so the sandboxed program is a
# shell: bwrap and the landlock helper take a program argv, not shell
# syntax. Absolute path because PATH inside the sandbox is whatever the
# caller handed us.
BWRAP_SHELL = "/bin/sh"
LANDLOCK_SHELL = "/bin/sh"

# Landlock ABI versions (the kernel reports the highest it supports):
# 1 = 5.13 (file system rights), 2 = 5.19 (REFER), 3 = 6.2 (TRUNCATE),
# 4 = 6.7 (handled_access_net, TCP), 10 = UDP rights.
LANDLOCK_ABI_FILESYSTEM = 1
LANDLOCK_ABI_REFER = 2
LANDLOCK_ABI_TRUNCATE = 3
LANDLOCK_ABI_NETWORK = 4
LANDLOCK_ABI_UDP = 10

# A hung backend probe must not stall a spawn path; the probe runs a single
# trivial command, so 10s is generous.
_PROBE_TIMEOUT_SECONDS = 10.0
# The probe command is a pure shell builtin: nothing is exec'd, nothing is
# written, no network. If this cannot exit 0 under the sandbox, nothing can.
_PROBE_COMMAND = "exit 0"

# Runner abstraction for probe(): the default executes argv directly;
# tests inject a fake to assert argv shape without spawning anything.
# (Same contract as os_sandbox.ProbeRunner — that alias is re-used.)

_INSTALL_HINTS: dict[str, str] = {
    BACKEND_BUBBLEWRAP: (
        "Debian/Ubuntu: apt install bubblewrap; Fedora: dnf install bubblewrap; "
        "Arch: pacman -S bubblewrap"
    ),
    BACKEND_LANDLOCK: (
        "requires Linux >= 5.13 (>= 6.7 for network denial), or install "
        "bubblewrap (apt install bubblewrap) and use sandbox.backend=bubblewrap"
    ),
}


# ---------------------------------------------------------------------------
# Landlock kernel ABI probe
# ---------------------------------------------------------------------------


def probe_landlock_abi() -> int:
    """Kernel Landlock ABI version (0 = unsupported); safe on any platform.

    Queries ``landlock_create_ruleset`` with ``LANDLOCK_CREATE_RULESET_VERSION``,
    a pure query that creates nothing and restricts nothing. Off Linux this
    returns 0 without issuing a syscall — syscall 444 means something else
    entirely on the BSDs.
    """
    if sys.platform != "linux":
        return 0
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long
        version = libc.syscall(
            ctypes.c_long(444),  # SYS_landlock_create_ruleset
            ctypes.c_void_p(None),
            ctypes.c_size_t(0),
            ctypes.c_uint(1),  # LANDLOCK_CREATE_RULESET_VERSION
        )
    except (OSError, AttributeError):
        return 0
    return max(0, int(version))


def _safe_abi(abi_query: Callable[[], int] | None) -> tuple[int, str]:
    """(abi version, failure detail) for *abi_query*, never raising.

    A query that errors (seccomp EPERM, a broken stub) reads as ABI 0 plus
    a detail string so the typed error can say *why* the kernel looked
    unsupported instead of silently claiming it is.
    """
    query = abi_query if abi_query is not None else probe_landlock_abi
    try:
        return max(0, int(query())), ""
    except OSError as exc:
        return 0, f"; ABI query failed: {exc}"
    except (TypeError, ValueError):
        return 0, ""


def _default_probe_runner(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        capture_output=True,
        check=False,
        timeout=_PROBE_TIMEOUT_SECONDS,
    )


# ---------------------------------------------------------------------------
# bubblewrap backend
# ---------------------------------------------------------------------------


def _writable_binds(request: SandboxRequest) -> list[str]:
    """Writable bind targets for one request, ancestors before descendants.

    bwrap mounts in argv order, so a child directory bound before its
    ancestor would be hidden by the later ancestor mount. ``/dev/null`` is
    excluded (the fresh ``--dev /dev`` tree already provides a writable null
    device) and ``/`` is never bound writable — that would undo the
    read-only root. Sources that do not exist on this host are skipped:
    there is nothing to bind and nothing could write them anyway.
    """
    paths = [
        path
        for path in writable_subpaths(request)
        if path not in ("/dev/null", "/") and os.path.exists(path)
    ]
    return sorted(paths, key=lambda path: (path.count("/"), path))


def _bwrap_argv(executable: str, command: str, request: SandboxRequest) -> list[str]:
    """argv form of one bubblewrap run (used by probe and wrap)."""
    argv = [
        executable,
        # Read-only root: everything not re-mounted below stays read-only.
        "--ro-bind",
        "/",
        "/",
        # Fresh /dev (writable null/random inside) and /proc; /sys stays
        # read-only from the root bind.
        "--dev",
        "/dev",
        "--tmpfs",
        "/dev/shm",
        "--proc",
        "/proc",
    ]
    for path in _writable_binds(request):
        argv.extend(("--bind", path, path))
    if not request.allow_network:
        # A fresh, empty network namespace: no outbound, no bind, no loopback.
        argv.append("--unshare-net")
    argv.extend(("--die-with-parent", "--new-session", BWRAP_SHELL, "-c", command))
    return argv


class BubblewrapBackend:
    """bubblewrap backend: wraps commands with a read-only-root namespace.

    *which* / *runner* / *platform* are injection points for tests; the
    defaults resolve the binary on PATH, run the probe subprocess and read
    ``sys.platform``.
    """

    name = BACKEND_BUBBLEWRAP

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
        """True on Linux with ``bwrap`` resolvable on PATH."""
        return self._platform == "linux" and self._which(BUBBLEWRAP_EXECUTABLE) is not None

    def probe(self) -> bool:
        """Live check: run one builtin-only command under the sandbox."""
        executable = self._which(BUBBLEWRAP_EXECUTABLE)
        if executable is None:
            return False
        argv = _bwrap_argv(executable, _PROBE_COMMAND, SandboxRequest(cwd=os.getcwd()))
        try:
            completed = self._runner(argv)
        except (OSError, subprocess.SubprocessError):
            return False
        return completed.returncode == 0

    def wrap(self, command: str, request: SandboxRequest) -> str:
        """Return ``bwrap ... /bin/sh -c <command>`` as a shell string.

        Pure string work: every token — including the inner command — is
        embedded with :func:`shlex.quote`, so the outer shell round-trips
        the argv verbatim and the inner shell sees the original command
        unchanged.
        """
        executable = self._which(BUBBLEWRAP_EXECUTABLE)
        if executable is None:
            raise OperationUnsupported(
                f"sandbox backend {self.name!r} cannot wrap: "
                f"{BUBBLEWRAP_EXECUTABLE} not usable here"
            )
        return " ".join(shlex.quote(part) for part in _bwrap_argv(executable, command, request))


# ---------------------------------------------------------------------------
# Landlock backend
# ---------------------------------------------------------------------------

# The helper is the whole landlock backend's runtime: it re-queries the
# kernel ABI itself (so the wrap string stays valid across kernel
# versions), builds a ruleset that handles exactly the write-shaped
# accesses that ABI knows, adds one PATH_BENEATH rule per writable path,
# sets NO_NEW_PRIVS, restricts itself and execs the wrapped command. It is
# embedded in every wrapped command as ``<python> -c <helper> ...``.
LANDLOCK_HELPER = r"""import ctypes
import os
import sys

# Landlock has no glibc wrappers: the three syscalls are invoked raw. The
# numbers are stable across x86_64/aarch64 and the newer unified ports.
_SYS_CREATE_RULESET = 444
_SYS_ADD_RULE = 445
_SYS_RESTRICT_SELF = 446
_CREATE_RULESET_VERSION = 1  # query-only flag: returns the ABI, creates nothing
_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38

# LANDLOCK_ACCESS_FS_* bits (uapi/linux/landlock.h), stable since 5.13.
_FS_WRITE_FILE = 1 << 1
_FS_REMOVE_DIR = 1 << 4
_FS_REMOVE_FILE = 1 << 5
_FS_MAKE_CHAR = 1 << 6
_FS_MAKE_DIR = 1 << 7
_FS_MAKE_REG = 1 << 8
_FS_MAKE_SOCK = 1 << 9
_FS_MAKE_FIFO = 1 << 10
_FS_MAKE_BLOCK = 1 << 11
_FS_MAKE_SYM = 1 << 12
_FS_REFER = 1 << 13  # ABI 2 (5.19)
_FS_TRUNCATE = 1 << 14  # ABI 3 (6.2)
_NET_BIND_TCP = 1 << 0
_NET_CONNECT_TCP = 1 << 1
_NET_BIND_UDP = 1 << 2  # ABI 10
_NET_CONNECT_SEND_UDP = 1 << 3  # ABI 10
_NET_ABI = 4  # first ABI with handled_access_net (6.7)
_UDP_ABI = 10

_ERROR_EXIT = 77


def _fail(message):
    sys.stderr.write("landlock: %s\n" % message)
    raise SystemExit(_ERROR_EXIT)


def _syscall(libc, number, *args):
    libc.syscall.restype = ctypes.c_long
    result = libc.syscall(ctypes.c_long(number), *args)
    if result < 0:
        _fail("syscall %d failed: %s" % (number, os.strerror(ctypes.get_errno())))
    return result


def _parse(args):
    writables, allow_net, probe = [], False, False
    while args:
        arg = args.pop(0)
        if arg == "--writable":
            if not args:
                _fail("--writable needs a path")
            writables.append(args.pop(0))
        elif arg == "--allow-net":
            allow_net = True
        elif arg == "--probe":
            probe = True
        elif arg == "--":
            break
        else:
            sys.stderr.write("landlock: unexpected argument %r\n" % arg)
            raise SystemExit(2)
    return writables, allow_net, probe, args


def main(argv):
    writables, allow_net, probe, command = _parse(list(argv))
    libc = ctypes.CDLL(None, use_errno=True)
    abi = _syscall(libc, _SYS_CREATE_RULESET, None, ctypes.c_size_t(0),
                   ctypes.c_uint(_CREATE_RULESET_VERSION))
    if abi < 1:
        _fail("kernel Landlock ABI %d unsupported (needs >= 1)" % abi)

    # Handle exactly the write-shaped accesses this kernel knows about;
    # reads stay open because they are not handled at all.
    handled = (_FS_WRITE_FILE | _FS_REMOVE_DIR | _FS_REMOVE_FILE
               | _FS_MAKE_CHAR | _FS_MAKE_DIR | _FS_MAKE_REG | _FS_MAKE_SOCK
               | _FS_MAKE_FIFO | _FS_MAKE_BLOCK | _FS_MAKE_SYM)
    if abi >= 2:
        handled |= _FS_REFER
    if abi >= 3:
        handled |= _FS_TRUNCATE
    # Network is only deniable from ABI 4 on; on ABI >= 10 UDP is covered too.
    net = 0
    if not allow_net and abi >= _NET_ABI:
        net = _NET_BIND_TCP | _NET_CONNECT_TCP
        if abi >= _UDP_ABI:
            net |= _NET_BIND_UDP | _NET_CONNECT_SEND_UDP

    if net:
        class RulesetAttr(ctypes.Structure):
            _fields_ = [("handled_access_fs", ctypes.c_uint64),
                        ("handled_access_net", ctypes.c_uint64)]
        ruleset_attr = RulesetAttr(handled, net)
    else:
        class RulesetAttr(ctypes.Structure):
            _fields_ = [("handled_access_fs", ctypes.c_uint64)]
        ruleset_attr = RulesetAttr(handled)
    ruleset_fd = _syscall(libc, _SYS_CREATE_RULESET, ctypes.byref(ruleset_attr),
                          ctypes.c_size_t(ctypes.sizeof(ruleset_attr)), ctypes.c_uint(0))

    class PathBeneathAttr(ctypes.Structure):
        _pack_ = 1  # the kernel struct is __attribute__((packed)), 12 bytes
        _fields_ = [("allowed_access", ctypes.c_uint64),
                    ("parent_fd", ctypes.c_int32)]

    for path in writables:
        try:
            parent_fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
        except OSError as exc:
            # A missing/unreadable configured path cannot be written anyway.
            sys.stderr.write("landlock: no write rule for %s: %s\n" % (path, exc))
            continue
        rule = PathBeneathAttr(handled, parent_fd)
        _syscall(libc, _SYS_ADD_RULE, ctypes.c_int(ruleset_fd),
                 ctypes.c_int(_RULE_PATH_BENEATH), ctypes.byref(rule),
                 ctypes.c_uint(0))
        os.close(parent_fd)

    # restrict_self requires NO_NEW_PRIVS, and both are irreversible: that
    # is why this helper runs (and dies) as a child, never in the caller.
    if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        _fail("prctl(PR_SET_NO_NEW_PRIVS) failed: %s" % os.strerror(ctypes.get_errno()))
    _syscall(libc, _SYS_RESTRICT_SELF, ctypes.c_int(ruleset_fd), ctypes.c_uint(0))
    if probe:
        return 0
    if not command:
        sys.stderr.write("landlock: missing command\n")
        return 2
    try:
        os.execvp(command[0], command)
    except OSError as exc:
        sys.stderr.write("landlock: exec %s failed: %s\n" % (command[0], exc))
        return 127
    return 127


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
"""


class LandlockBackend:
    """Landlock backend: wraps commands behind a kernel ABI ruleset.

    *runner* / *platform* / *abi_query* / *python* are injection points for
    tests; the defaults run the probe subprocess, read ``sys.platform``,
    query the kernel ABI via :func:`probe_landlock_abi` and use the running
    interpreter for the helper.
    """

    name = BACKEND_LANDLOCK

    def __init__(
        self,
        *,
        runner: ProbeRunner | None = None,
        platform: str | None = None,
        abi_query: Callable[[], int] | None = None,
        python: str | None = None,
    ) -> None:
        self._runner: ProbeRunner = runner if runner is not None else _default_probe_runner
        self._platform = sys.platform if platform is None else platform
        self._abi_query = abi_query if abi_query is not None else probe_landlock_abi
        self._python = python if python is not None else (sys.executable or "python3")

    def _abi(self) -> int:
        return _safe_abi(self._abi_query)[0]

    def available(self) -> bool:
        """True on Linux whose kernel reports Landlock ABI >= 1."""
        return self._platform == "linux" and self._abi() >= LANDLOCK_ABI_FILESYSTEM

    def probe(self) -> bool:
        """Live check: restrict a throwaway child, then let it exit 0.

        The probe never restricts the caller — ``landlock_restrict_self``
        is irreversible, so the whole syscall chain is exercised in the
        helper child instead.
        """
        if not self.available():
            return False
        try:
            completed = self._runner(self._helper_argv((), probe=True))
        except (OSError, subprocess.SubprocessError):
            return False
        return completed.returncode == 0

    def wrap(self, command: str, request: SandboxRequest) -> str:
        """Return ``<python> -c <helper> ... -- /bin/sh -c <command>``.

        Pure string work; the inner command round-trips verbatim through
        :func:`shlex.quote`. Fail-closed on what this kernel cannot
        deliver: below ABI 1 nothing can be confined, and below ABI 4 a
        requested network denial cannot be honoured — the wrap raises
        :class:`OperationUnsupported` instead of spawning unconfined.
        """
        abi = self._abi()
        if abi < LANDLOCK_ABI_FILESYSTEM:
            raise OperationUnsupported(
                f"sandbox backend {self.name!r} cannot wrap: kernel Landlock "
                f"ABI {abi} < {LANDLOCK_ABI_FILESYSTEM} (requires Linux >= 5.13)"
            )
        if not request.allow_network and abi < LANDLOCK_ABI_NETWORK:
            raise OperationUnsupported(
                f"sandbox backend {self.name!r} cannot deny network on Landlock "
                f"ABI {abi}: handled_access_net needs ABI >= {LANDLOCK_ABI_NETWORK} "
                "(Linux >= 6.7); install bubblewrap and use "
                "sandbox.backend=bubblewrap, or set sandbox.allow_network=true"
            )
        return " ".join(
            shlex.quote(part)
            for part in self._helper_argv(
                writable_subpaths(request),
                allow_network=request.allow_network,
                command=command,
            )
        )

    def _helper_argv(
        self,
        writables: Iterable[str],
        *,
        allow_network: bool = False,
        probe: bool = False,
        command: str = "",
    ) -> list[str]:
        argv = [self._python, "-c", LANDLOCK_HELPER]
        if probe:
            # Probe mode restricts a throwaway child and exits — no command.
            argv.append("--probe")
        else:
            for path in writables:
                argv.extend(("--writable", path))
            if allow_network:
                argv.append("--allow-net")
            argv.extend(("--", LANDLOCK_SHELL, "-c", command))
        return argv


# ---------------------------------------------------------------------------
# Resolution: config value -> ready backend (fail-closed)
# ---------------------------------------------------------------------------


def preferred_linux_backend(
    *,
    which: Callable[[str], str | None] = shutil.which,
    platform: str | None = None,
    abi_query: Callable[[], int] | None = None,
) -> str | None:
    """The best usable Linux confinement backend, bubblewrap first.

    Returns ``"bubblewrap"`` when ``bwrap`` is resolvable on Linux,
    ``"landlock"`` when only the kernel ABI is there, and ``None`` when
    neither is usable (including off Linux). Tooling that wants to suggest
    a backend (typed errors, future doctor output) should go through this
    so the preference lives in exactly one place.
    """
    resolved_platform = sys.platform if platform is None else platform
    if resolved_platform != "linux":
        return None
    if which(BUBBLEWRAP_EXECUTABLE) is not None:
        return BACKEND_BUBBLEWRAP
    if _safe_abi(abi_query)[0] >= LANDLOCK_ABI_FILESYSTEM:
        return BACKEND_LANDLOCK
    return None


def _alternative_hint(
    name: str,
    *,
    platform: str,
    which: Callable[[str], str | None],
    abi_query: Callable[[], int] | None,
) -> str:
    """ ", or switch to sandbox.backend=... (usable on this host)" when the
    *other* Linux backend is usable — bubblewrap's preference order means a
    missing bwrap on a Landlock kernel (and vice versa) is never a dead end.
    """
    preferred = preferred_linux_backend(platform=platform, which=which, abi_query=abi_query)
    if preferred is not None and preferred != name:
        return f", or switch to sandbox.backend={preferred!r} (usable on this host)"
    return ""


def _unsupported(name: str, detail: str, *, alternative: str = "") -> OperationUnsupported:
    return OperationUnsupported(
        f"sandbox backend {name!r} is not available on this host ({detail})"
        f"{alternative}; install hints: {_INSTALL_HINTS[name]}; supported backends: "
        + ", ".join(SUPPORTED_BACKENDS)
    )


def _availability_detail(
    name: str,
    *,
    platform: str,
    which: Callable[[str], str | None],
    abi_query: Callable[[], int] | None,
) -> str:
    if name == BACKEND_BUBBLEWRAP:
        return f"platform={platform}, {BUBBLEWRAP_EXECUTABLE} not usable here"
    abi, failure = _safe_abi(abi_query)
    return (
        f"platform={platform}, Landlock kernel ABI {abi} < {LANDLOCK_ABI_FILESYSTEM}: "
        f"requires Linux >= 5.13{failure}"
    )


def resolve_linux_backend(
    name: str,
    *,
    which: Callable[[str], str | None] = shutil.which,
    runner: ProbeRunner | None = None,
    platform: str | None = None,
    abi_query: Callable[[], int] | None = None,
    python: str | None = None,
) -> SandboxBackend:
    """Turn a Linux ``sandbox.backend`` value into a ready backend.

    Fail-closed via :meth:`~nexuscli.policy.os_sandbox.SandboxBackend.available`
    and :meth:`~nexuscli.policy.os_sandbox.SandboxBackend.probe`: anything
    not usable on this host raises :class:`OperationUnsupported` with
    install hints instead of downgrading to an unconfined spawn.

    *which* / *runner* / *platform* are the test injection points shared
    with the Seatbelt path; *abi_query* and *python* are Landlock-specific.
    """
    if name not in LINUX_BACKENDS:
        raise OperationUnsupported(
            f"unknown sandbox backend {name!r}; supported backends: "
            + ", ".join(SUPPORTED_BACKENDS)
        )
    resolved_platform = sys.platform if platform is None else platform
    if name == BACKEND_BUBBLEWRAP:
        backend: SandboxBackend = BubblewrapBackend(
            which=which, runner=runner, platform=resolved_platform
        )
    else:
        backend = LandlockBackend(
            runner=runner,
            platform=resolved_platform,
            abi_query=abi_query,
            python=python,
        )
    if not backend.available():
        raise _unsupported(
            name,
            _availability_detail(
                name, platform=resolved_platform, which=which, abi_query=abi_query
            ),
            alternative=_alternative_hint(
                name, platform=resolved_platform, which=which, abi_query=abi_query
            ),
        )
    if not backend.probe():
        raise OperationUnsupported(
            f"sandbox backend {name!r} probe failed: a trivial command did not "
            "survive confinement on this host; supported backends: " + ", ".join(SUPPORTED_BACKENDS)
        )
    return backend
