"""Docker sandbox backend: argv shape, fail-closed resolution, config surface.

All tests inject fake which/runner (the contract test_os_sandbox.py and
test_linux_sandbox.py established): nothing spawns a real docker, and the
daemon is not required to be running.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from nexuscli.config import SandboxConfig
from nexuscli.policy.docker_sandbox import (
    DOCKER_EXECUTABLE,
    DockerBackend,
    resolve_docker_backend,
)
from nexuscli.policy.os_sandbox import (
    DEFAULT_DOCKER_IMAGE,
    SUPPORTED_BACKENDS,
    OperationUnsupported,
    SandboxRequest,
    resolve_backend,
    wrap_command,
)

_FAKE_DOCKER = "/usr/local/bin/docker"


def _ok(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(argv, 0, "", "")


def _fail(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(argv, 1, "", "Cannot connect to the Docker daemon")


def _which_docker(name: str) -> str | None:
    return _FAKE_DOCKER if name == DOCKER_EXECUTABLE else None


def _request(cwd: Path, **overrides: object) -> SandboxRequest:
    return SandboxRequest(cwd=str(cwd), **overrides)  # type: ignore[arg-type]


def _wrapped_argv(wrapped: str) -> list[str]:
    # wrap() returns a single shell string with every token shlex-quoted;
    # the outer shell therefore parses it back into exactly these tokens.
    return shlex.split(wrapped)


class TestWrapArgvShape:
    def test_read_only_rm_network_none_tmpfs_and_cwd_mount(self, tmp_path: Path) -> None:
        backend = DockerBackend(which=_which_docker, runner=_ok)
        wrapped = backend.wrap("echo hi", _request(tmp_path))
        argv = _wrapped_argv(wrapped)
        assert argv[0] == _FAKE_DOCKER
        assert argv[1:4] == ["run", "--rm", "--read-only"]
        # network isolation is on by default, tmpfs /tmp provides the temp tree
        assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
        assert "--tmpfs" in argv and argv[argv.index("--tmpfs") + 1] == "/tmp"
        # cwd is bind mounted at the same path and is the workdir
        cwd = str(tmp_path)
        assert f"{cwd}:{cwd}" in argv
        assert "-w" in argv and argv[argv.index("-w") + 1] == cwd
        # default image, inner shell, verbatim command
        assert DEFAULT_DOCKER_IMAGE in argv
        shell_at = argv.index("/bin/sh")
        assert argv[shell_at - 1] == DEFAULT_DOCKER_IMAGE
        assert argv[shell_at + 1] == "-c" and argv[shell_at + 2] == "echo hi"

    def test_allow_network_drops_isolation_flag(self, tmp_path: Path) -> None:
        backend = DockerBackend(which=_which_docker, runner=_ok)
        argv = _wrapped_argv(backend.wrap("true", _request(tmp_path, allow_network=True)))
        assert "--network" not in argv

    def test_writable_paths_mounted_deduped_and_absolutized(self, tmp_path: Path) -> None:
        extra = tmp_path / "out"
        extra.mkdir()
        backend = DockerBackend(which=_which_docker, runner=_ok)
        request = _request(
            tmp_path,
            writable_paths=(str(extra), str(extra) + "/", "./out"),
        )
        argv = _wrapped_argv(backend.wrap("true", request))
        mount = f"{extra}:{extra}"
        # the cwd mount plus exactly one mount for the duplicated extra
        assert argv.count(mount) == 1

    def test_missing_writable_paths_skipped_not_mounted(self, tmp_path: Path) -> None:
        backend = DockerBackend(which=_which_docker, runner=_ok)
        request = _request(tmp_path, writable_paths=(str(tmp_path / "nope"),))
        argv = _wrapped_argv(backend.wrap("true", request))
        assert "nope" not in " ".join(argv)

    def test_docker_image_from_request(self, tmp_path: Path) -> None:
        backend = DockerBackend(which=_which_docker, runner=_ok)
        argv = _wrapped_argv(backend.wrap("true", _request(tmp_path, docker_image="alpine:3.20")))
        assert "alpine:3.20" in argv and DEFAULT_DOCKER_IMAGE not in argv

    def test_command_with_quotes_and_semicolons_round_trips(self, tmp_path: Path) -> None:
        backend = DockerBackend(which=_which_docker, runner=_ok)
        nasty = "echo 'a b'; rm -rf \"$HOME/$(echo x)\""
        argv = _wrapped_argv(backend.wrap(nasty, _request(tmp_path)))
        assert argv[-1] == nasty


class TestFailClosed:
    def test_available_flips_with_which(self) -> None:
        assert DockerBackend(which=_which_docker).available() is True
        assert DockerBackend(which=lambda _name: None).available() is False

    def test_probe_requires_cli_and_daemon(self) -> None:
        assert DockerBackend(which=_which_docker, runner=_ok).probe() is True
        assert DockerBackend(which=_which_docker, runner=_fail).probe() is False
        assert DockerBackend(which=lambda _name: None, runner=_ok).probe() is False

    def test_probe_runner_exception_reads_false(self) -> None:
        def _boom(argv: list[str]) -> subprocess.CompletedProcess:
            raise subprocess.TimeoutExpired(argv, 10)

        assert DockerBackend(which=_which_docker, runner=_boom).probe() is False

    def test_resolve_unavailable_cli_raises_operation_unsupported(self) -> None:
        with pytest.raises(OperationUnsupported) as exc:
            resolve_docker_backend("docker", which=lambda _name: None, runner=_ok)
        assert "not available" in str(exc.value)
        assert "supported backends" in str(exc.value)

    def test_resolve_daemon_down_raises_operation_unsupported(self) -> None:
        with pytest.raises(OperationUnsupported) as exc:
            resolve_docker_backend("docker", which=_which_docker, runner=_fail)
        assert "probe failed" in str(exc.value) and "daemon" in str(exc.value)

    def test_resolve_unknown_name_lists_supported_backends(self) -> None:
        with pytest.raises(OperationUnsupported) as exc:
            resolve_docker_backend("podman", which=_which_docker, runner=_ok)
        assert all(name in str(exc.value) for name in SUPPORTED_BACKENDS)

    def test_wrap_without_cli_raises(self, tmp_path: Path) -> None:
        backend = DockerBackend(which=lambda _name: None, runner=_ok)
        with pytest.raises(OperationUnsupported):
            backend.wrap("true", _request(tmp_path))


class TestResolutionDispatch:
    def test_backend_name_registered(self) -> None:
        assert "docker" in SUPPORTED_BACKENDS

    def test_resolve_backend_lazy_imports_docker(self) -> None:
        backend = resolve_backend("docker", which=_which_docker, runner=_ok)
        assert backend is not None and backend.name == "docker"

    def test_wrap_command_carries_docker_image(self, tmp_path: Path) -> None:
        wrapped = wrap_command(
            "echo hi",
            backend="docker",
            cwd=str(tmp_path),
            which=_which_docker,
            runner=_ok,
            docker_image="alpine:3.20",
        )
        assert "alpine:3.20" in _wrapped_argv(wrapped)

    def test_wrap_command_default_image(self, tmp_path: Path) -> None:
        wrapped = wrap_command(
            "echo hi", backend="docker", cwd=str(tmp_path), which=_which_docker, runner=_ok
        )
        assert DEFAULT_DOCKER_IMAGE in _wrapped_argv(wrapped)


class TestConfigSurface:
    def test_default_docker_image(self) -> None:
        assert SandboxConfig().docker_image == DEFAULT_DOCKER_IMAGE

    def test_docker_image_override_survives_request_roundtrip(self, tmp_path: Path) -> None:
        config = replace(SandboxConfig(), docker_image="python:3.12-slim")
        request = SandboxRequest(cwd=str(tmp_path), docker_image=config.docker_image)
        assert request.docker_image == "python:3.12-slim"
