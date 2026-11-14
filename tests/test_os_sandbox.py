"""Tests for the OS-sandbox abstraction (policy/os_sandbox.py, task 0026).

Unit level runs entirely on fakes — an injected ``which`` for binary
availability and an injected probe runner recording argv — so the profile
shape, the argv escaping and the fail-closed errors hold on every platform
without touching a real sandbox. The real ``sandbox-exec`` cases are
conditioned on macOS with the binary present (and a non-root uid), and skip
everywhere else.
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
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexuscli.config import NexusCliConfig, SandboxConfig, load_config
from nexuscli.policy import CommandGuard
from nexuscli.policy.command_guard import CommandPolicyError
from nexuscli.policy.os_sandbox import (
    SEATBELT_EXECUTABLE,
    SUPPORTED_BACKENDS,
    OperationUnsupported,
    SandboxRequest,
    SeatbeltBackend,
    build_seatbelt_profile,
    resolve_backend,
    wrap_command,
)
from nexuscli.tools import get_builtin_tools
from nexuscli.tools.base import ToolContext
from nexuscli.tools.commands import CommandExecutor
from nexuscli.tools.registry import ToolRegistry
from tests.project_trust_support import trust_project_config

FAKE_SANDBOX_EXEC = "/usr/bin/sandbox-exec"


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


def _write_block(profile: str) -> str:
    """The subpath list inside the ``(allow file-write* ...)`` form."""
    after = profile.split("(allow file-write*", 1)[1]
    return after.split("\n)", 1)[0]


def _recording_backend():
    """Structural SandboxBackend stand-in: records what it is asked to wrap."""
    wrapped: list[str] = []

    class _Backend:
        name = "seatbelt"

        def available(self) -> bool:
            return True

        def probe(self) -> bool:
            return True

        def wrap(self, command: str, request: SandboxRequest) -> str:
            wrapped.append(command)
            return command

    return _Backend(), wrapped


# ---------------------------------------------------------------------------
# Profile generation
# ---------------------------------------------------------------------------


def test_profile_denies_by_default_and_confines_writes(tmp_path: Path):
    extra = tmp_path / "extra-allow"
    profile = build_seatbelt_profile(
        SandboxRequest(cwd=str(tmp_path), writable_paths=(str(extra),))
    )

    assert "(version 1)" in profile
    assert "(deny default)" in profile
    # Reads stay open; writes are confined to the allow list below.
    assert "(allow file-read*)" in profile
    write_block = _write_block(profile)
    for writable in (tmp_path, os.path.realpath(tmp_path), extra, Path("/dev/null")):
        assert f'(subpath "{writable}")' in write_block, writable
    # Network is explicitly denied when not requested.
    assert "(deny network*)" in profile
    assert "(allow network-outbound)" not in profile


def test_profile_includes_system_temp_tree(tmp_path: Path):
    profile = build_seatbelt_profile(SandboxRequest(cwd=str(tmp_path)))

    write_block = _write_block(profile)
    tempdir = tempfile.gettempdir()
    assert f'(subpath "{tempdir}")' in write_block
    assert '(subpath "/tmp")' in write_block
    assert '(subpath "/private/tmp")' in write_block
    # The same directory spelled through its realpath must not be duplicated.
    assert write_block.count(f'(subpath "{os.path.realpath(tempdir)}")') == 1


def test_profile_allows_outbound_network_only_when_requested(tmp_path: Path):
    denied = build_seatbelt_profile(SandboxRequest(cwd=str(tmp_path)))
    allowed = build_seatbelt_profile(SandboxRequest(cwd=str(tmp_path), allow_network=True))

    assert "(deny network*)" in denied
    assert "(allow network-outbound)" not in denied
    assert "(allow network-outbound)" in allowed
    # Bind/inbound stay denied even with outbound granted.
    assert "(deny default)" in allowed
    assert "(allow network-bind)" not in allowed


def test_profile_escapes_quotes_in_writable_paths(tmp_path: Path):
    tricky = os.path.normpath(str(tmp_path / 'we"ird\\path'))
    profile = build_seatbelt_profile(SandboxRequest(cwd=str(tricky)))

    escaped = tricky.replace("\\", "\\\\").replace('"', '\\"')
    assert f'(subpath "{escaped}")' in profile
    # The raw, unescaped spelling must not leak into the profile.
    assert f'(subpath "{tricky}")' not in profile


def test_profile_deduplicates_writable_paths(tmp_path: Path):
    profile = build_seatbelt_profile(
        SandboxRequest(cwd=str(tmp_path), writable_paths=(str(tmp_path), str(tmp_path)))
    )

    real = os.path.realpath(tmp_path)
    assert profile.count(f'(subpath "{tmp_path}")') == 1
    assert profile.count(f'(subpath "{real}")') == 1


# ---------------------------------------------------------------------------
# Parameter escaping (wrap)
# ---------------------------------------------------------------------------

_NASTY_COMMAND = """echo "double 'quoted'" '$HOME' `pwd` ; rm -rf /tmp/nope | tee x"""


def test_wrap_round_trips_command_and_profile_verbatim(tmp_path: Path):
    backend = SeatbeltBackend(platform="darwin")
    request = SandboxRequest(cwd=str(tmp_path))

    wrapped = backend.wrap(_NASTY_COMMAND, request)

    tokens = shlex.split(wrapped)
    assert tokens[0] == SEATBELT_EXECUTABLE
    assert tokens[1] == "-p"
    assert tokens[2] == build_seatbelt_profile(request)
    assert tokens[3] == "/bin/sh"
    assert tokens[4] == "-c"
    assert tokens[5] == _NASTY_COMMAND


def test_wrap_profile_survives_as_single_argument(tmp_path: Path):
    backend = SeatbeltBackend(platform="darwin")
    wrapped = backend.wrap("echo hi", SandboxRequest(cwd=str(tmp_path)))

    tokens = shlex.split(wrapped)
    profile = tokens[2]
    assert profile.startswith("(version 1)")
    assert "\n" in profile  # multi-line profile, still one argv element


# ---------------------------------------------------------------------------
# Resolution: fail-closed semantics
# ---------------------------------------------------------------------------


def test_resolve_none_returns_none():
    assert resolve_backend("none") is None


def test_resolve_unknown_backend_fails_closed():
    with pytest.raises(OperationUnsupported) as excinfo:
        resolve_backend("bubblewrap")
    # The message names the offender and the supported set, and the request
    # is never silently treated as "none".
    assert "bubblewrap" in str(excinfo.value)
    for backend in SUPPORTED_BACKENDS:
        assert backend in str(excinfo.value)


def test_resolve_seatbelt_off_macos_fails_closed():
    with pytest.raises(OperationUnsupported) as excinfo:
        resolve_backend("seatbelt", platform="linux", which=_fake_which(FAKE_SANDBOX_EXEC))
    assert "seatbelt" in str(excinfo.value)


def test_resolve_seatbelt_without_binary_fails_closed():
    with pytest.raises(OperationUnsupported) as excinfo:
        resolve_backend("seatbelt", platform="darwin", which=_fake_which(None))
    assert SEATBELT_EXECUTABLE in str(excinfo.value)


def test_resolve_probe_failure_fails_closed():
    with pytest.raises(OperationUnsupported):
        resolve_backend(
            "seatbelt",
            platform="darwin",
            which=_fake_which(FAKE_SANDBOX_EXEC),
            runner=_fake_runner(returncode=1),
        )


def test_resolve_probe_crash_fails_closed():
    with pytest.raises(OperationUnsupported):
        resolve_backend(
            "seatbelt",
            platform="darwin",
            which=_fake_which(FAKE_SANDBOX_EXEC),
            runner=_fake_runner(fail=True),
        )


def test_resolve_probe_success_returns_ready_backend():
    runner = _fake_runner(returncode=0)
    backend = resolve_backend(
        "seatbelt", platform="darwin", which=_fake_which(FAKE_SANDBOX_EXEC), runner=runner
    )

    assert isinstance(backend, SeatbeltBackend)
    # The probe really went through sandbox-exec with a profile and the
    # trivial builtin-only command.
    (call,) = runner.calls  # type: ignore[attr-defined]
    assert call[0] == FAKE_SANDBOX_EXEC
    assert call[1] == "-p"
    assert call[2].startswith("(version 1)")
    assert call[3] == "/bin/sh"
    assert call[4] == "-c"
    assert call[5] == "exit 0"


def test_seatbelt_probe_reports_missing_binary():
    runner = _fake_runner()
    backend = SeatbeltBackend(platform="darwin", which=_fake_which(None), runner=runner)

    assert backend.available() is False
    assert backend.probe() is False
    assert runner.calls == []  # the probe never spawned anything


def test_wrap_command_none_is_identity():
    command = "echo 'unchanged'"
    assert wrap_command(command, backend="none") == command


def test_wrap_command_wraps_with_request(tmp_path: Path):
    wrapped = wrap_command(
        "echo hi",
        backend="seatbelt",
        cwd=str(tmp_path),
        writable_paths=("/opt/data",),
        allow_network=True,
        platform="darwin",
        which=_fake_which(FAKE_SANDBOX_EXEC),
        runner=_fake_runner(returncode=0),
    )

    tokens = shlex.split(wrapped)
    assert tokens[:2] == [SEATBELT_EXECUTABLE, "-p"]
    assert tokens[3:5] == ["/bin/sh", "-c"]
    assert tokens[5] == "echo hi"
    assert f'(subpath "{tmp_path}")' in tokens[2]
    assert '(subpath "/opt/data")' in tokens[2]
    assert "(allow network-outbound)" in tokens[2]


# ---------------------------------------------------------------------------
# Config surface
# ---------------------------------------------------------------------------


def test_sandbox_config_defaults_to_none():
    config = load_config(project_root=None, env={})

    assert config.sandbox.backend == "none"
    assert config.sandbox.writable_paths == []
    assert config.sandbox.allow_network is False


def test_sandbox_config_loads_from_project_json(tmp_path: Path):
    project_config = tmp_path / ".nexuscli"
    project_config.mkdir()
    (project_config / "config.json").write_text(
        json.dumps(
            {
                "sandbox": {
                    "backend": "seatbelt",
                    "writable_paths": ["/opt/data"],
                    "allow_network": True,
                    "unknown_key": "dropped",
                }
            }
        ),
        encoding="utf-8",
    )
    # sandbox.* is a project security-sensitive section: assert the trusted
    # branch explicitly (fail-closed is covered by test_project_trust.py).
    trust_project_config(tmp_path)

    config = load_config(project_root=tmp_path, env={})

    assert config.sandbox == SandboxConfig(
        backend="seatbelt", writable_paths=["/opt/data"], allow_network=True
    )


# ---------------------------------------------------------------------------
# CommandExecutor wiring
# ---------------------------------------------------------------------------


def test_executor_default_runs_without_sandbox(tmp_path: Path):
    async def scenario():
        executor = CommandExecutor()
        return await executor.execute("echo plain", cwd=str(tmp_path))

    result = asyncio.run(scenario())

    assert result.success
    assert "plain" in result.stdout


def test_executor_none_backend_runs_without_sandbox(tmp_path: Path):
    async def scenario():
        executor = CommandExecutor(sandbox=SandboxConfig(backend="none"))
        return await executor.execute("echo plain", cwd=str(tmp_path))

    result = asyncio.run(scenario())

    assert result.success
    assert "plain" in result.stdout


def test_executor_unavailable_backend_fails_closed(monkeypatch: pytest.MonkeyPatch):
    def failing_resolve(name, **_kwargs):
        raise OperationUnsupported(f"sandbox backend {name!r} is not available on this host")

    monkeypatch.setattr("nexuscli.tools.commands.resolve_backend", failing_resolve)

    with pytest.raises(OperationUnsupported):
        CommandExecutor(sandbox=SandboxConfig(backend="seatbelt"))


def test_executor_wraps_at_spawn_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The wrapped string is what actually spawns, with the original command
    carried through verbatim."""

    class _PrintfBackend:
        name = "seatbelt"

        def available(self) -> bool:
            return True

        def probe(self) -> bool:
            return True

        def wrap(self, command: str, request: SandboxRequest) -> str:
            # A sandbox stand-in that survives the spawn and carries the
            # original command: `printf %s <quoted command>`.
            return f"printf %s {shlex.quote(command)}"

    monkeypatch.setattr(
        "nexuscli.tools.commands.resolve_backend", lambda name, **_kwargs: _PrintfBackend()
    )
    executor = CommandExecutor(sandbox=SandboxConfig(backend="seatbelt"))

    async def scenario():
        return await executor.execute("echo wrapped-run", cwd=str(tmp_path))

    result = asyncio.run(scenario())

    assert result.success
    assert result.stdout.strip() == "echo wrapped-run"


def test_executor_guard_validates_original_command_before_wrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A blacklisted command must trip the command guard — the sandbox never
    gets to replace the string the policies saw."""

    backend, wrapped = _recording_backend()
    monkeypatch.setattr("nexuscli.tools.commands.resolve_backend", lambda name, **_kwargs: backend)
    executor = CommandExecutor(
        command_guard=CommandGuard(blacklist=["sudo"]),
        sandbox=SandboxConfig(backend="seatbelt"),
    )

    async def scenario():
        return await executor.execute("sudo ls", cwd=str(tmp_path))

    with pytest.raises(CommandPolicyError):
        asyncio.run(scenario())

    assert wrapped == []  # never wrapped, never spawned


# ---------------------------------------------------------------------------
# Real sandbox-exec (macOS only; skip-conditioned)
# ---------------------------------------------------------------------------


def _real_seatbelt_usable() -> bool:
    if sys.platform != "darwin":
        return False
    if shutil.which(SEATBELT_EXECUTABLE) is None:
        return False
    return os.geteuid() != 0


_REAL = pytest.mark.skipif(
    not _real_seatbelt_usable(),
    reason="real sandbox-exec smoke: macOS with sandbox-exec on PATH, non-root only",
)


@_REAL
def test_real_seatbelt_probe_passes():
    backend = SeatbeltBackend()

    assert backend.available()
    assert backend.probe()


@_REAL
def test_real_seatbelt_wrapped_command_runs_in_cwd(tmp_path: Path):
    """The minimal profile must actually be usable: a write into the
    command's cwd succeeds under the real sandbox."""
    backend = SeatbeltBackend()
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


@_REAL
def test_real_seatbelt_denies_write_outside_writable_domains(tmp_path: Path):
    backend = SeatbeltBackend()
    escape_target = Path("/private/etc/nexuscli-sbx-escape-probe")
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


@_REAL
def test_real_seatbelt_denies_network_by_default(tmp_path: Path):
    """Loopback connect fails under ``(deny network*)`` while the same
    connect without the sandbox succeeds (control)."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    probe = (
        "import socket\n"
        f"socket.create_connection(('127.0.0.1', {port}), timeout=2)\n"
        "print('connected')\n"
    )
    try:
        backend = SeatbeltBackend()
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
        # Control: the unsandboxed child connects fine.
        control = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            timeout=10,
        )
    finally:
        server.close()

    assert control.returncode == 0, control.stderr
    assert sandboxed.returncode != 0
    assert "connected" not in sandboxed.stdout


# ---------------------------------------------------------------------------
# 生产接线：模型面 bash 工具从 context.config.sandbox 取配置（task 0026 接线）
# ---------------------------------------------------------------------------


def _bash_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    return registry


def _bash_context(tmp_path: Path, sandbox: SandboxConfig) -> ToolContext:
    config = NexusCliConfig()
    config.features.audit_log = False
    config.sandbox = sandbox
    return ToolContext(
        cwd=str(tmp_path), config=config, approval_callback=lambda payload: "approve"
    )


def test_bash_tool_fails_closed_when_backend_unavailable(tmp_path):
    """请求了本机无法交付的后端：命令在 spawn 之前被拒，fail-closed 报错。"""
    result = asyncio.run(
        _bash_registry()
        .get("bash")
        .execute(
            {"command": "echo hi"},
            _bash_context(tmp_path, SandboxConfig(backend="no-such-backend")),
        )
    )

    assert result.is_error
    assert "unknown sandbox backend 'no-such-backend'" in result.content


@_REAL
def test_real_bash_tool_wraps_when_seatbelt_configured(tmp_path):
    """backend="seatbelt" 时生产 bash 路径真的包了 sandbox-exec：网络被拒、cwd 内可写。"""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    probe = (
        "import socket\n"
        f"socket.create_connection(('127.0.0.1', {port}), timeout=2)\n"
        "print('connected')\n"
    )
    command = f"{sys.executable} -c {shlex.quote(probe)}"
    registry = _bash_registry()
    try:
        sandboxed = asyncio.run(
            registry.get("bash").execute(
                {"command": command},
                _bash_context(tmp_path, SandboxConfig(backend="seatbelt")),
            )
        )
        # 对照组：默认 backend=none 时同一命令照常连上（默认行为不变）
        control = asyncio.run(
            registry.get("bash").execute(
                {"command": command},
                _bash_context(tmp_path, SandboxConfig()),
            )
        )
        # 最小可用 profile：cwd 内写入照常成功
        inside = asyncio.run(
            registry.get("bash").execute(
                {"command": "echo ok > inside.txt"},
                _bash_context(tmp_path, SandboxConfig(backend="seatbelt")),
            )
        )
    finally:
        server.close()

    assert control.is_error is False
    assert "connected" in control.content
    assert sandboxed.is_error is True
    assert "connected" not in sandboxed.content
    assert inside.is_error is False, inside.content
    assert (tmp_path / "inside.txt").read_text(encoding="utf-8").strip() == "ok"
