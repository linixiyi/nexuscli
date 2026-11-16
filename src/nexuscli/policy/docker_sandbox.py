"""docker_sandbox.py — Docker container backend of the OS sandbox.

Implements the :class:`~nexuscli.policy.os_sandbox.SandboxBackend` protocol
for ``sandbox.backend = "docker"``: every wrapped command runs inside a
one-shot container —

- ``--rm``: the container is deleted when it exits; nothing persists.
- ``--read-only``: the container root filesystem is read-only.
- ``--network=none``: no network at all unless ``sandbox.allow_network``
  drops the flag (container network is then fully open — bind included —
  which is coarser than Seatbelt's outbound-only profile; see the honest
  boundaries below).
- ``--tmpfs /tmp``: a writable temp tree *inside* the container. Unlike the
  host backends this is NOT the host temp directory — files written to
  ``/tmp`` vanish with the container.
- The command's cwd and every explicit ``writable_paths`` entry are bind
  mounted read-write at the same absolute path, so relative paths keep
  resolving. Host paths that do not exist are skipped (nothing could write
  them anyway).

The image defaults to ``python:3.11-slim`` and is configurable via
``sandbox.docker_image``; the first use of an image pulls it from the
registry, which needs network access on the *host* side.

Fail-closed contract, same as every other backend: no ``docker`` CLI, or a
daemon that does not answer, raises
:class:`~nexuscli.policy.os_sandbox.OperationUnsupported` — the spawn never
silently degrades to ``none``.

Honest boundaries:

- ``allow_network`` is all-or-nothing: Docker cannot express Seatbelt's
  "outbound only, bind/inbound denied" split without unportable iptables
  work inside the container.
- macOS/Windows bind mounts go through the VM file-sharing layer
  (Docker Desktop); paths outside the shared roots are not visible in the
  container and writes to them cannot succeed.
- A container is heavier than the host backends: each command pays
  container start-up cost, and image pulls download from the network.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from collections.abc import Callable

from nexuscli.policy.os_sandbox import (
    BACKEND_DOCKER,
    SUPPORTED_BACKENDS,
    OperationUnsupported,
    ProbeRunner,
    SandboxBackend,
    SandboxRequest,
)

DOCKER_EXECUTABLE = "docker"
# The wrapped command is a shell string, so the sandboxed program is a
# shell inside the container (the default python:*-slim image ships one).
DOCKER_SHELL = "/bin/sh"

# Host paths that must never be bind mounted: /dev/null exists in every
# container, and the host temp tree is deliberately NOT shared (the
# container gets its own --tmpfs /tmp instead).
_SKIP_MOUNTS = frozenset({"/dev/null", "/tmp", "/private/tmp"})

# A hung backend probe must not stall a spawn path; `docker version`
# answers fast when the daemon is up.
_PROBE_TIMEOUT_SECONDS = 10.0

_INSTALL_HINT = (
    "install Docker Desktop (or a docker engine) and make sure the daemon "
    "is running; first use of an image pulls it from the registry"
)


def _default_probe_runner(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        capture_output=True,
        check=False,
        timeout=_PROBE_TIMEOUT_SECONDS,
    )


def _mount_targets(request: SandboxRequest) -> list[str]:
    """Bind-mount targets for one request: cwd plus explicit writables.

    Everything else from :func:`writable_subpaths` — the temp spellings and
    ``/dev/null`` — is deliberately excluded (see ``_SKIP_MOUNTS``): the
    container has its own ``/tmp`` tmpfs and null device. Host paths that
    do not exist are skipped, mirroring the bubblewrap backend.
    """
    targets: list[str] = []
    seen: set[str] = set()
    for path in (request.cwd, *request.writable_paths):
        resolved = os.path.realpath(path)
        if resolved in seen or resolved in _SKIP_MOUNTS or not os.path.exists(resolved):
            continue
        seen.add(resolved)
        targets.append(resolved)
    return targets


def _docker_argv(executable: str, command: str, request: SandboxRequest) -> list[str]:
    """argv form of one docker run (shared by probe reasoning and wrap)."""
    cwd = os.path.realpath(request.cwd)
    argv = [
        executable,
        "run",
        # One-shot container: removed on exit, read-only root filesystem.
        "--rm",
        "--read-only",
        # Writable temp inside the container (not the host temp tree).
        "--tmpfs",
        "/tmp",
    ]
    if not request.allow_network:
        # Fully isolated network stack: no outbound, no bind, no loopback.
        # allow_network drops the flag and with it the whole isolation —
        # see the module docstring for the granularity caveat.
        argv.extend(("--network", "none"))
    argv.extend(("-v", f"{cwd}:{cwd}", "-w", cwd))
    for path in _mount_targets(request):
        argv.extend(("-v", f"{path}:{path}"))
    argv.extend((request.docker_image, DOCKER_SHELL, "-c", command))
    return argv


class DockerBackend:
    """Docker backend: wraps commands in a one-shot container.

    *which* / *runner* are injection points for tests; the defaults resolve
    the CLI on PATH and run the probe subprocess. Unlike the OS backends
    this one is cross-platform — availability is only about the ``docker``
    CLI, and the daemon is what ``probe()`` actually exercises.
    """

    name = BACKEND_DOCKER

    def __init__(
        self,
        *,
        which: Callable[[str], str | None] = shutil.which,
        runner: ProbeRunner | None = None,
    ) -> None:
        self._which = which
        self._runner: ProbeRunner = runner if runner is not None else _default_probe_runner

    def available(self) -> bool:
        """True wherever the ``docker`` CLI resolves on PATH."""
        return self._which(DOCKER_EXECUTABLE) is not None

    def probe(self) -> bool:
        """Live check: the CLI *and* the daemon must answer.

        ``docker version`` fails on a missing socket exactly like it fails
        on a missing CLI, so this one command covers both halves of "a
        container could actually run here". The image is deliberately not
        pulled here: probing must stay fast and offline-friendly.
        """
        executable = self._which(DOCKER_EXECUTABLE)
        if executable is None:
            return False
        try:
            completed = self._runner([executable, "version"])
        except (OSError, subprocess.SubprocessError):
            return False
        return completed.returncode == 0

    def wrap(self, command: str, request: SandboxRequest) -> str:
        """Return ``docker run ... /bin/sh -c <command>`` as a shell string.

        Pure string work: every token — including the inner command — is
        embedded with :func:`shlex.quote`, so the outer shell round-trips
        the argv verbatim and the inner shell sees the original command
        unchanged. The image comes from the request
        (``sandbox.docker_image``), not the constructor: the executor
        resolves backends once at start-up but learns the configured image
        per request.
        """
        executable = self._which(DOCKER_EXECUTABLE)
        if executable is None:
            raise OperationUnsupported(
                f"sandbox backend {self.name!r} cannot wrap: {DOCKER_EXECUTABLE} not usable here"
            )
        return " ".join(shlex.quote(part) for part in _docker_argv(executable, command, request))


def resolve_docker_backend(
    name: str,
    *,
    which: Callable[[str], str | None] = shutil.which,
    runner: ProbeRunner | None = None,
    platform: str | None = None,
) -> SandboxBackend:
    """Turn ``"docker"`` into a ready backend (fail-closed).

    Mirrors :func:`nexuscli.policy.linux_sandbox.resolve_linux_backend`:
    anything not usable on this host raises :class:`OperationUnsupported`
    with install hints instead of downgrading to an unconfined spawn.
    *platform* is accepted for signature parity with the other resolvers
    and deliberately unused — docker is cross-platform.
    """
    del platform  # cross-platform backend: no platform gate
    if name != BACKEND_DOCKER:
        raise OperationUnsupported(
            f"unknown sandbox backend {name!r}; supported backends: "
            + ", ".join(SUPPORTED_BACKENDS)
        )
    backend = DockerBackend(which=which, runner=runner)
    if not backend.available():
        raise OperationUnsupported(
            f"sandbox backend {name!r} is not available on this host: "
            f"{DOCKER_EXECUTABLE} not usable here; install hints: {_INSTALL_HINT}; "
            "supported backends: " + ", ".join(SUPPORTED_BACKENDS)
        )
    if not backend.probe():
        raise OperationUnsupported(
            f"sandbox backend {name!r} probe failed: the docker daemon did not "
            f"answer `docker version`; install hints: {_INSTALL_HINT}; "
            "supported backends: " + ", ".join(SUPPORTED_BACKENDS)
        )
    return backend
