"""Tests for the Linux sandbox backends (policy/linux_sandbox.py, task 0027).

Unit level runs entirely on fakes — an injected ``which`` for binary
availability, an injected probe runner recording argv, and an injected
kernel-ABI query for Landlock — so the bwrap argv shape, the helper
round-trip and the fail-closed errors hold on every platform without
touching a real sandbox. The real ``bwrap`` / Landlock cases are
conditioned on Linux with a *working* backend (a live probe must pass
before the smoke cases run) and skip everywhere else.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexuscli.config import SandboxConfig, load_config
from nexuscli.policy import CommandGuard
from nexuscli.policy.command_guard import CommandPolicyError
from nexuscli.policy.linux_sandbox import (
    BUBBLEWRAP_EXECUTABLE,
    LANDLOCK_ABI_NETWORK,
    LANDLOCK_HELPER,
    BubblewrapBackend,
    LandlockBackend,
    preferred_linux_backend,
    probe_landlock_abi,
    resolve_linux_backend,
)
from nexuscli.policy.os_sandbox import (
    SUPPORTED_BACKENDS,
    OperationUnsupported,
    SandboxRequest,
    resolve_backend,
    wrap_command,
)
from nexuscli.tools.commands import CommandExecutor
from tests.project_trust_support import trust_project_config

FAKE_BWRAP = "/usr/bin/bwrap"
FAKE_PYTHON = "/usr/bin/fake-python"

_NASTY_COMMAND = """echo "double 'quoted'" '$HOME' `pwd` ; rm -rf /tmp/nope | tee x"""


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _fake_runner(returncode: int = 0, *, fail: bool = False):
    """Probe runner stub: records argv, returns a CompletedProcess-like."""
    calls: list[list[str]] = []

    def run(argv: list[str]):
        calls.append(list(argv))
        if fail:
            raise OSError("spawn failed")
        return SimpleNamespace(returncode=returncode)

    run.calls = calls  # type: ignore[attr-defined]
    return run


def _fake_which(path: str | None):
    return lambda _name: path


def _fake_abi(version: int):
    return lambda: version


def _failing_abi(exc: Exception):
    def query() -> int:
        raise exc

    return query


def _bind_sources(tokens: list[str]) -> list[str]:
    """The source path of every ``--bind <src> <dst>`` pair in *tokens*."""
    return [tokens[i + 1] for i, token in enumerate(tokens) if token == "--bind"]


def _writable_paths(tokens: list[str]) -> list[str]:
    """The path of every ``--writable <path>`` flag in *tokens*."""
    return [tokens[i + 1] for i, token in enumerate(tokens) if token == "--writable"]


# ---------------------------------------------------------------------------
# bubblewrap argv: read-only root, writable workspace, network toggle
# ---------------------------------------------------------------------------


def test_bwrap_argv_binds_root_readonly_with_fresh_dev_proc(tmp_path: Path):
    backend = BubblewrapBackend(platform="linux", which=_fake_which(FAKE_BWRAP))
    tokens = shlex.split(backend.wrap("echo hi", SandboxRequest(cwd=str(tmp_path))))

    assert tokens[0] == FAKE_BWRAP
    assert tokens[1:4] == ["--ro-bind", "/", "/"]
    assert tokens[4:6] == ["--dev", "/dev"]
    assert tokens[6:8] == ["--tmpfs", "/dev/shm"]
    assert tokens[8:10] == ["--proc", "/proc"]
    assert "--die-with-parent" in tokens
    assert "--new-session" in tokens


def test_bwrap_round_trips_inner_command_verbatim(tmp_path: Path):
    backend = BubblewrapBackend(platform="linux", which=_fake_which(FAKE_BWRAP))
    tokens = shlex.split(backend.wrap(_NASTY_COMMAND, SandboxRequest(cwd=str(tmp_path))))

    assert tokens[-3:] == ["/bin/sh", "-c", _NASTY_COMMAND]


def test_bwrap_binds_cwd_and_writable_extras(tmp_path: Path):
    extra = tmp_path / "extra-allow"
    extra.mkdir()
    backend = BubblewrapBackend(platform="linux", which=_fake_which(FAKE_BWRAP))
    tokens = shlex.split(
        backend.wrap(
            "echo hi",
            SandboxRequest(cwd=str(tmp_path), writable_paths=(str(extra),)),
        )
    )

    sources = _bind_sources(tokens)
    assert str(tmp_path) in sources
    assert str(extra) in sources
    # Bound at the same path: the command's cwd survives the wrap.
    for i, token in enumerate(tokens):
        if token == "--bind" and tokens[i + 1] == str(tmp_path):
            assert tokens[i + 2] == str(tmp_path)
            break
    else:
        pytest.fail("cwd not bound at its own path")


def test_bwrap_binds_system_temp_tree(tmp_path: Path):
    backend = BubblewrapBackend(platform="linux", which=_fake_which(FAKE_BWRAP))
    tokens = shlex.split(backend.wrap("echo hi", SandboxRequest(cwd=str(tmp_path))))

    sources = _bind_sources(tokens)
    assert "/tmp" in sources
    assert "/private/tmp" in sources  # macOS spelling of the same tree


def test_bwrap_never_binds_dev_null_or_root_writable(tmp_path: Path):
    backend = BubblewrapBackend(platform="linux", which=_fake_which(FAKE_BWRAP))
    tokens = shlex.split(
        backend.wrap(
            "echo hi",
            SandboxRequest(cwd=str(tmp_path), writable_paths=("/dev/null", "/")),
        )
    )

    sources = _bind_sources(tokens)
    assert "/dev/null" not in sources
    assert "/" not in sources


def test_bwrap_deduplicates_writable_binds(tmp_path: Path):
    backend = BubblewrapBackend(platform="linux", which=_fake_which(FAKE_BWRAP))
    tokens = shlex.split(
        backend.wrap(
            "echo hi",
            SandboxRequest(cwd=str(tmp_path), writable_paths=(str(tmp_path), str(tmp_path))),
        )
    )

    assert _bind_sources(tokens).count(str(tmp_path)) == 1


def test_bwrap_binds_ancestors_before_descendants(tmp_path: Path):
    """A child bind mounted before its ancestor would be hidden by it —
    bwrap mounts in argv order, so cwd must come after any directory that
    contains it."""
    nested = tmp_path / "nested"
    nested.mkdir()
    backend = BubblewrapBackend(platform="linux", which=_fake_which(FAKE_BWRAP))
    tokens = shlex.split(
        backend.wrap(
            "echo hi",
            SandboxRequest(cwd=str(nested), writable_paths=(str(tmp_path),)),
        )
    )

    assert _bind_sources(tokens).index("/tmp") < _bind_sources(tokens).index(str(nested))
    assert _bind_sources(tokens).index(str(tmp_path)) < _bind_sources(tokens).index(str(nested))


def test_bwrap_drops_nonexistent_writable_paths(tmp_path: Path):
    backend = BubblewrapBackend(platform="linux", which=_fake_which(FAKE_BWRAP))
    tokens = shlex.split(
        backend.wrap(
            "echo hi",
            SandboxRequest(cwd=str(tmp_path), writable_paths=("/opt/nexuscli-sbx-missing",)),
        )
    )

    assert "/opt/nexuscli-sbx-missing" not in _bind_sources(tokens)


def test_bwrap_network_toggle(tmp_path: Path):
    backend = BubblewrapBackend(platform="linux", which=_fake_which(FAKE_BWRAP))
    denied = shlex.split(backend.wrap("echo hi", SandboxRequest(cwd=str(tmp_path))))
    allowed = shlex.split(
        backend.wrap("echo hi", SandboxRequest(cwd=str(tmp_path), allow_network=True))
    )

    assert "--unshare-net" in denied
    assert "--unshare-net" not in allowed


# ---------------------------------------------------------------------------
# bubblewrap availability + probe
# ---------------------------------------------------------------------------


def test_bwrap_available_on_linux_with_binary():
    backend = BubblewrapBackend(platform="linux", which=_fake_which(FAKE_BWRAP))

    assert backend.available() is True


def test_bwrap_unavailable_off_linux():
    backend = BubblewrapBackend(platform="darwin", which=_fake_which(FAKE_BWRAP))

    assert backend.available() is False


def test_bwrap_unavailable_without_binary():
    backend = BubblewrapBackend(platform="linux", which=_fake_which(None))

    assert backend.available() is False


def test_bwrap_probe_runs_trivial_command_under_sandbox():
    runner = _fake_runner(returncode=0)
    backend = BubblewrapBackend(platform="linux", which=_fake_which(FAKE_BWRAP), runner=runner)

    assert backend.probe() is True
    (call,) = runner.calls  # type: ignore[attr-defined]
    assert call[0] == FAKE_BWRAP
    assert call[1:4] == ["--ro-bind", "/", "/"]
    assert "--unshare-net" in call
    assert call[-3:] == ["/bin/sh", "-c", "exit 0"]


def test_bwrap_probe_reports_missing_binary():
    runner = _fake_runner()
    backend = BubblewrapBackend(platform="linux", which=_fake_which(None), runner=runner)

    assert backend.available() is False
    assert backend.probe() is False
    assert runner.calls == []  # the probe never spawned anything


def test_bwrap_probe_failure_is_not_available():
    backend = BubblewrapBackend(
        platform="linux",
        which=_fake_which(FAKE_BWRAP),
        runner=_fake_runner(returncode=1),
    )

    assert backend.probe() is False


def test_bwrap_probe_crash_is_not_available():
    backend = BubblewrapBackend(
        platform="linux",
        which=_fake_which(FAKE_BWRAP),
        runner=_fake_runner(fail=True),
    )

    assert backend.probe() is False


def test_bwrap_wrap_without_binary_fails_closed():
    backend = BubblewrapBackend(platform="linux", which=_fake_which(None))

    with pytest.raises(OperationUnsupported):
        backend.wrap("echo hi", SandboxRequest(cwd="/tmp"))


# ---------------------------------------------------------------------------
# Landlock: kernel-ABI availability + probe
# ---------------------------------------------------------------------------


def test_landlock_abi_probe_is_safe_off_linux():
    # On non-Linux hosts the query must return 0 without issuing syscall
    # 444 (which means something else entirely on the BSDs).
    if sys.platform == "linux":
        pytest.skip("meaningful only off Linux")
    assert probe_landlock_abi() == 0


def test_landlock_available_with_kernel_abi():
    backend = LandlockBackend(platform="linux", abi_query=_fake_abi(4))

    assert backend.available() is True


def test_landlock_unavailable_without_kernel_abi():
    backend = LandlockBackend(platform="linux", abi_query=_fake_abi(0))

    assert backend.available() is False


def test_landlock_unavailable_off_linux():
    backend = LandlockBackend(platform="darwin", abi_query=_fake_abi(4))

    assert backend.available() is False


def test_landlock_abi_query_permission_denied_is_unavailable():
    backend = LandlockBackend(platform="linux", abi_query=_failing_abi(PermissionError("denied")))

    assert backend.available() is False


def test_landlock_abi_query_oserror_is_unavailable():
    backend = LandlockBackend(platform="linux", abi_query=_failing_abi(OSError("blocked")))

    assert backend.available() is False


def test_landlock_probe_restricts_a_throwaway_child():
    runner = _fake_runner(returncode=0)
    backend = LandlockBackend(
        platform="linux", abi_query=_fake_abi(4), runner=runner, python=FAKE_PYTHON
    )

    assert backend.probe() is True
    (call,) = runner.calls  # type: ignore[attr-defined]
    assert call[0] == FAKE_PYTHON
    assert call[1:3] == ["-c", LANDLOCK_HELPER]
    assert call[3] == "--probe"


def test_landlock_probe_never_spawns_when_unavailable():
    runner = _fake_runner()
    backend = LandlockBackend(platform="linux", abi_query=_fake_abi(0), runner=runner)

    assert backend.probe() is False
    assert runner.calls == []  # restrict_self must never run in the caller


def test_landlock_probe_failure_is_not_available():
    backend = LandlockBackend(
        platform="linux",
        abi_query=_fake_abi(4),
        runner=_fake_runner(returncode=1),
    )

    assert backend.probe() is False


def test_landlock_probe_crash_is_not_available():
    backend = LandlockBackend(
        platform="linux",
        abi_query=_fake_abi(4),
        runner=_fake_runner(fail=True),
    )

    assert backend.probe() is False


# ---------------------------------------------------------------------------
# Landlock wrap: helper argv, writable rules, network fail-closed
# ---------------------------------------------------------------------------


def test_landlock_helper_is_valid_python_and_pins_the_kernel_abi():
    compile(LANDLOCK_HELPER, "<landlock-helper>", "exec")
    # Raw syscall numbers and the MAKE_REG bit position are the load-bearing
    # constants: a changed kernel header would break confinement silently.
    assert "_SYS_CREATE_RULESET = 444" in LANDLOCK_HELPER
    assert "_SYS_ADD_RULE = 445" in LANDLOCK_HELPER
    assert "_SYS_RESTRICT_SELF = 446" in LANDLOCK_HELPER
    assert "_FS_MAKE_REG = 1 << 8" in LANDLOCK_HELPER


def test_landlock_wrap_runs_helper_then_shell_verbatim(tmp_path: Path):
    backend = LandlockBackend(platform="linux", abi_query=_fake_abi(4), python=FAKE_PYTHON)
    tokens = shlex.split(backend.wrap(_NASTY_COMMAND, SandboxRequest(cwd=str(tmp_path))))

    assert tokens[0] == FAKE_PYTHON
    assert tokens[1] == "-c"
    assert tokens[2] == LANDLOCK_HELPER  # arrives as one argv element
    assert tokens[-3:] == ["/bin/sh", "-c", _NASTY_COMMAND]
    assert "--" in tokens


def test_landlock_wrap_grants_writable_rules(tmp_path: Path):
    extra = tmp_path / "extra-allow"
    extra.mkdir()
    backend = LandlockBackend(platform="linux", abi_query=_fake_abi(4))
    tokens = shlex.split(
        backend.wrap(
            "echo hi",
            SandboxRequest(cwd=str(tmp_path), writable_paths=(str(extra),)),
        )
    )

    writables = _writable_paths(tokens)
    for path in ("/dev/null", "/tmp", str(tmp_path), os.path.realpath(tmp_path), str(extra)):
        assert path in writables, path


def test_landlock_wrap_network_denied_by_default(tmp_path: Path):
    backend = LandlockBackend(platform="linux", abi_query=_fake_abi(4))
    tokens = shlex.split(backend.wrap("echo hi", SandboxRequest(cwd=str(tmp_path))))

    assert "--allow-net" not in tokens


def test_landlock_wrap_network_allowed_when_requested(tmp_path: Path):
    backend = LandlockBackend(platform="linux", abi_query=_fake_abi(2))
    tokens = shlex.split(
        backend.wrap("echo hi", SandboxRequest(cwd=str(tmp_path), allow_network=True))
    )

    assert "--allow-net" in tokens


def test_landlock_network_denial_needs_abi_4_fail_closed(tmp_path: Path):
    """On an ABI 3 kernel a requested network denial cannot be delivered —
    the wrap must fail, not spawn with an open network."""
    backend = LandlockBackend(platform="linux", abi_query=_fake_abi(3))

    with pytest.raises(OperationUnsupported) as excinfo:
        backend.wrap("echo hi", SandboxRequest(cwd=str(tmp_path)))

    message = str(excinfo.value)
    assert "ABI" in message
    assert "6.7" in message
    assert "bubblewrap" in message  # the preferred fallback


def test_landlock_wrap_without_kernel_abi_fails_closed(tmp_path: Path):
    backend = LandlockBackend(platform="linux", abi_query=_fake_abi(0))

    with pytest.raises(OperationUnsupported):
        backend.wrap("echo hi", SandboxRequest(cwd=str(tmp_path)))


# ---------------------------------------------------------------------------
# Preference order: bubblewrap first, Landlock fallback
# ---------------------------------------------------------------------------


def test_preferred_backend_is_bubblewrap_when_both_usable():
    assert (
        preferred_linux_backend(
            platform="linux", which=_fake_which(FAKE_BWRAP), abi_query=_fake_abi(4)
        )
        == "bubblewrap"
    )


def test_preferred_backend_falls_back_to_landlock():
    assert (
        preferred_linux_backend(platform="linux", which=_fake_which(None), abi_query=_fake_abi(1))
        == "landlock"
    )


def test_preferred_backend_none_when_neither_usable():
    assert (
        preferred_linux_backend(platform="linux", which=_fake_which(None), abi_query=_fake_abi(0))
        is None
    )


def test_preferred_backend_none_off_linux():
    assert (
        preferred_linux_backend(
            platform="darwin", which=_fake_which(FAKE_BWRAP), abi_query=_fake_abi(4)
        )
        is None
    )


# ---------------------------------------------------------------------------
# resolve_linux_backend: fail-closed semantics
# ---------------------------------------------------------------------------


def test_resolve_bubblewrap_returns_ready_backend():
    runner = _fake_runner(returncode=0)
    backend = resolve_linux_backend(
        "bubblewrap", platform="linux", which=_fake_which(FAKE_BWRAP), runner=runner
    )

    assert isinstance(backend, BubblewrapBackend)
    (call,) = runner.calls  # type: ignore[attr-defined]
    assert call[0] == FAKE_BWRAP


def test_resolve_bubblewrap_without_binary_names_install_hint():
    with pytest.raises(OperationUnsupported) as excinfo:
        resolve_linux_backend("bubblewrap", platform="linux", which=_fake_which(None))

    message = str(excinfo.value)
    assert "apt install bubblewrap" in message
    for backend_name in SUPPORTED_BACKENDS:
        assert backend_name in message


def test_resolve_bubblewrap_off_linux_fails_closed():
    with pytest.raises(OperationUnsupported) as excinfo:
        resolve_linux_backend("bubblewrap", platform="darwin", which=_fake_which(FAKE_BWRAP))

    assert "platform=darwin" in str(excinfo.value)


def test_resolve_bubblewrap_probe_failure_fails_closed():
    with pytest.raises(OperationUnsupported):
        resolve_linux_backend(
            "bubblewrap",
            platform="linux",
            which=_fake_which(FAKE_BWRAP),
            runner=_fake_runner(returncode=1),
        )


def test_resolve_bubblewrap_probe_crash_fails_closed():
    with pytest.raises(OperationUnsupported):
        resolve_linux_backend(
            "bubblewrap",
            platform="linux",
            which=_fake_which(FAKE_BWRAP),
            runner=_fake_runner(fail=True),
        )


def test_resolve_landlock_returns_ready_backend():
    runner = _fake_runner(returncode=0)
    backend = resolve_linux_backend(
        "landlock",
        platform="linux",
        runner=runner,
        abi_query=_fake_abi(4),
        python=FAKE_PYTHON,
    )

    assert isinstance(backend, LandlockBackend)
    (call,) = runner.calls  # type: ignore[attr-defined]
    assert call[0] == FAKE_PYTHON
    assert call[3] == "--probe"


def test_resolve_landlock_without_kernel_abi_points_at_bubblewrap():
    with pytest.raises(OperationUnsupported) as excinfo:
        resolve_linux_backend(
            "landlock",
            platform="linux",
            which=_fake_which(None),
            abi_query=_fake_abi(0),
        )

    message = str(excinfo.value)
    assert "Linux >= 5.13" in message
    assert "bubblewrap" in message  # preferred alternative + install hint
    for backend_name in SUPPORTED_BACKENDS:
        assert backend_name in message


def test_resolve_landlock_permission_denied_abi_query_fails_closed():
    """权限拒绝：the ABI query itself failing must surface as a typed
    error carrying the kernel-side reason, never a silent downgrade."""
    with pytest.raises(OperationUnsupported) as excinfo:
        resolve_linux_backend(
            "landlock",
            platform="linux",
            abi_query=_failing_abi(PermissionError("seccomp denied")),
        )

    assert "ABI query failed" in str(excinfo.value)


def test_resolve_landlock_probe_failure_fails_closed():
    with pytest.raises(OperationUnsupported):
        resolve_linux_backend(
            "landlock",
            platform="linux",
            runner=_fake_runner(returncode=1),
            abi_query=_fake_abi(4),
        )


def test_resolve_linux_unknown_backend_fails_closed():
    with pytest.raises(OperationUnsupported) as excinfo:
        resolve_linux_backend("seccomp")

    for backend_name in SUPPORTED_BACKENDS:
        assert backend_name in str(excinfo.value)


# ---------------------------------------------------------------------------
# os_sandbox.resolve_backend integration (config value -> Linux backend)
# ---------------------------------------------------------------------------


def test_os_resolve_backend_routes_bubblewrap():
    runner = _fake_runner(returncode=0)
    backend = resolve_backend(
        "bubblewrap", platform="linux", which=_fake_which(FAKE_BWRAP), runner=runner
    )

    assert isinstance(backend, BubblewrapBackend)


def test_os_resolve_backend_routes_landlock_off_linux_fail_closed():
    if sys.platform == "linux":
        pytest.skip("on Linux the real kernel ABI may genuinely support landlock")
    with pytest.raises(OperationUnsupported):
        resolve_backend("landlock")


def test_os_resolve_backend_unknown_backend_lists_all():
    with pytest.raises(OperationUnsupported) as excinfo:
        resolve_backend("nonsuch")

    for backend_name in SUPPORTED_BACKENDS:
        assert backend_name in str(excinfo.value)


def test_wrap_command_with_bubblewrap(tmp_path: Path):
    extra = tmp_path / "data"
    extra.mkdir()
    wrapped = wrap_command(
        "echo hi",
        backend="bubblewrap",
        cwd=str(tmp_path),
        writable_paths=(str(extra),),
        allow_network=True,
        platform="linux",
        which=_fake_which(FAKE_BWRAP),
        runner=_fake_runner(returncode=0),
    )

    tokens = shlex.split(wrapped)
    assert tokens[0] == FAKE_BWRAP
    assert "--unshare-net" not in tokens
    assert str(extra) in _bind_sources(tokens)
    assert tokens[-3:] == ["/bin/sh", "-c", "echo hi"]


# ---------------------------------------------------------------------------
# Config surface
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend", ["bubblewrap", "landlock"])
def test_sandbox_config_accepts_linux_backends(tmp_path: Path, backend: str):
    project_config = tmp_path / ".nexuscli"
    project_config.mkdir()
    (project_config / "config.json").write_text(
        json.dumps({"sandbox": {"backend": backend, "writable_paths": ["/opt/data"]}}),
        encoding="utf-8",
    )
    trust_project_config(tmp_path)

    config = load_config(project_root=tmp_path, env={})

    assert config.sandbox == SandboxConfig(backend=backend, writable_paths=["/opt/data"])


# ---------------------------------------------------------------------------
# CommandExecutor wiring
# ---------------------------------------------------------------------------


def test_executor_wraps_with_bubblewrap_at_spawn_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The configured Linux backend value flows through CommandExecutor and
    the wrapped string is what actually spawns."""

    class _PrintfBackend:
        name = "bubblewrap"

        def available(self) -> bool:
            return True

        def probe(self) -> bool:
            return True

        def wrap(self, command: str, request: SandboxRequest) -> str:
            return f"printf %s {shlex.quote(command)}"

    monkeypatch.setattr(
        "nexuscli.tools.commands.resolve_backend", lambda name, **_kwargs: _PrintfBackend()
    )
    executor = CommandExecutor(sandbox=SandboxConfig(backend="bubblewrap"))

    async def scenario():
        return await executor.execute("echo wrapped-run", cwd=str(tmp_path))

    result = asyncio.run(scenario())

    assert result.success
    assert result.stdout.strip() == "echo wrapped-run"


def test_executor_guard_validates_original_before_linux_wrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    wrapped: list[str] = []

    class _RecordingBackend:
        name = "landlock"

        def available(self) -> bool:
            return True

        def probe(self) -> bool:
            return True

        def wrap(self, command: str, request: SandboxRequest) -> str:
            wrapped.append(command)
            return command

    monkeypatch.setattr(
        "nexuscli.tools.commands.resolve_backend",
        lambda name, **_kwargs: _RecordingBackend(),
    )
    executor = CommandExecutor(
        command_guard=CommandGuard(blacklist=["sudo"]),
        sandbox=SandboxConfig(backend="landlock"),
    )

    async def scenario():
        return await executor.execute("sudo ls", cwd=str(tmp_path))

    with pytest.raises(CommandPolicyError):
        asyncio.run(scenario())

    assert wrapped == []  # never wrapped, never spawned


def test_executor_linux_backends_fail_closed_on_this_host():
    """Without a usable Linux backend the executor construction must raise
    typedly — on this (macOS) host both Linux backends are unavailable by
    platform gate alone."""
    if sys.platform == "linux":
        pytest.skip("on Linux a real backend may be present; unit fakes cover this")
    with pytest.raises(OperationUnsupported):
        CommandExecutor(sandbox=SandboxConfig(backend="bubblewrap"))
    with pytest.raises(OperationUnsupported):
        CommandExecutor(sandbox=SandboxConfig(backend="landlock"))


# ---------------------------------------------------------------------------
# Real bubblewrap (Linux only; skip-conditioned on a *working* backend)
# ---------------------------------------------------------------------------


def _real_bwrap_usable() -> bool:
    if sys.platform != "linux" or shutil.which(BUBBLEWRAP_EXECUTABLE) is None:
        return False
    try:
        return BubblewrapBackend().probe()
    except Exception:  # noqa: BLE001 — a broken sandbox must skip, not fail
        return False


_REAL_BWRAP = pytest.mark.skipif(
    not _real_bwrap_usable(),
    reason="real bubblewrap smoke: Linux with bwrap on PATH and a passing probe only",
)


@_REAL_BWRAP
def test_real_bwrap_probe_passes():
    backend = BubblewrapBackend()

    assert backend.available()
    assert backend.probe()


@_REAL_BWRAP
def test_real_bwrap_wrapped_command_runs_in_cwd(tmp_path: Path):
    backend = BubblewrapBackend()
    wrapped = backend.wrap("echo sandboxed > out.txt", SandboxRequest(cwd=str(tmp_path)))

    completed = subprocess.run(
        ["/bin/sh", "-c", wrapped],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / "out.txt").read_text(encoding="utf-8").strip() == "sandboxed"


@_REAL_BWRAP
def test_real_bwrap_denies_write_outside_writable_domains(tmp_path: Path):
    backend = BubblewrapBackend()
    escape_target = Path("/etc/nexuscli-sbx-escape-probe")
    wrapped = backend.wrap(f"echo blocked > {escape_target}", SandboxRequest(cwd=str(tmp_path)))

    completed = subprocess.run(
        ["/bin/sh", "-c", wrapped],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode != 0
    assert not escape_target.exists()


def _loopback_probe(port: int) -> str:
    return (
        "import socket\n"
        f"socket.create_connection(('127.0.0.1', {port}), timeout=2)\n"
        "print('connected')\n"
    )


@_REAL_BWRAP
def test_real_bwrap_denies_loopback_by_default(tmp_path: Path):
    """``--unshare-net`` gives a fresh empty network namespace: the same
    connect that succeeds without the sandbox fails inside it."""
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        probe = _loopback_probe(port)
        backend = BubblewrapBackend()
        wrapped = backend.wrap(
            f"{sys.executable} -c {shlex.quote(probe)}", SandboxRequest(cwd=str(tmp_path))
        )
        sandboxed = subprocess.run(
            ["/bin/sh", "-c", wrapped],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            timeout=30,
        )
        control = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            timeout=10,
        )

    assert control.returncode == 0, control.stderr
    assert sandboxed.returncode != 0
    assert "connected" not in sandboxed.stdout


@_REAL_BWRAP
def test_real_bwrap_allows_loopback_when_requested(tmp_path: Path):
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        probe = _loopback_probe(port)
        backend = BubblewrapBackend()
        wrapped = backend.wrap(
            f"{sys.executable} -c {shlex.quote(probe)}",
            SandboxRequest(cwd=str(tmp_path), allow_network=True),
        )
        sandboxed = subprocess.run(
            ["/bin/sh", "-c", wrapped],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            timeout=30,
        )

    assert sandboxed.returncode == 0, sandboxed.stderr
    assert "connected" in sandboxed.stdout


# ---------------------------------------------------------------------------
# Real Landlock (Linux only; skip-conditioned on a *working* backend)
# ---------------------------------------------------------------------------


def _real_landlock_backend() -> LandlockBackend | None:
    if sys.platform != "linux":
        return None
    try:
        backend = LandlockBackend()
        if backend.available() and backend.probe():
            return backend
    except Exception:  # noqa: BLE001 — a broken sandbox must skip, not fail
        return None
    return None


_REAL_LANDLOCK = pytest.mark.skipif(
    _real_landlock_backend() is None,
    reason="real Landlock smoke: Linux with kernel ABI >= 1 and a passing probe only",
)


@_REAL_LANDLOCK
def test_real_landlock_probe_passes():
    backend = _real_landlock_backend()

    assert backend is not None
    assert backend.available()
    assert backend.probe()


@_REAL_LANDLOCK
def test_real_landlock_wrapped_command_runs_in_cwd(tmp_path: Path):
    backend = _real_landlock_backend()
    assert backend is not None
    if probe_landlock_abi() < LANDLOCK_ABI_NETWORK:
        pytest.skip("network toggle needs ABI >= 4; wrap fails closed below it")
    wrapped = backend.wrap("echo sandboxed > out.txt", SandboxRequest(cwd=str(tmp_path)))

    completed = subprocess.run(
        ["/bin/sh", "-c", wrapped],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / "out.txt").read_text(encoding="utf-8").strip() == "sandboxed"


@_REAL_LANDLOCK
def test_real_landlock_denies_write_outside_writable_domains(tmp_path: Path):
    backend = _real_landlock_backend()
    assert backend is not None
    if probe_landlock_abi() < LANDLOCK_ABI_NETWORK:
        pytest.skip("network toggle needs ABI >= 4; wrap fails closed below it")
    escape_target = Path("/etc/nexuscli-sbx-escape-probe")
    wrapped = backend.wrap(f"echo blocked > {escape_target}", SandboxRequest(cwd=str(tmp_path)))

    completed = subprocess.run(
        ["/bin/sh", "-c", wrapped],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode != 0
    assert not escape_target.exists()


@_REAL_LANDLOCK
def test_real_landlock_denies_loopback_by_default(tmp_path: Path):
    """With handled_access_net (ABI >= 4) a loopback connect fails under the
    ruleset while the unsandboxed control connects fine."""
    backend = _real_landlock_backend()
    assert backend is not None
    if probe_landlock_abi() < LANDLOCK_ABI_NETWORK:
        pytest.skip("network denial needs ABI >= 4")
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        probe = _loopback_probe(port)
        wrapped = backend.wrap(
            f"{sys.executable} -c {shlex.quote(probe)}", SandboxRequest(cwd=str(tmp_path))
        )
        sandboxed = subprocess.run(
            ["/bin/sh", "-c", wrapped],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            timeout=30,
        )
        control = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            timeout=10,
        )

    assert control.returncode == 0, control.stderr
    assert sandboxed.returncode != 0
    assert "connected" not in sandboxed.stdout


@_REAL_LANDLOCK
def test_real_landlock_allows_loopback_when_requested(tmp_path: Path):
    backend = _real_landlock_backend()
    assert backend is not None
    if probe_landlock_abi() < LANDLOCK_ABI_NETWORK:
        pytest.skip("network toggle needs ABI >= 4; wrap fails closed below it")
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        probe = _loopback_probe(port)
        wrapped = backend.wrap(
            f"{sys.executable} -c {shlex.quote(probe)}",
            SandboxRequest(cwd=str(tmp_path), allow_network=True),
        )
        sandboxed = subprocess.run(
            ["/bin/sh", "-c", wrapped],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            timeout=30,
        )

    assert sandboxed.returncode == 0, sandboxed.stderr
    assert "connected" in sandboxed.stdout
