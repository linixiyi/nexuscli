"""docker_image must flow from SandboxConfig into both wrap call sites.

The bash tool's ``wrap_command(...)`` and ``CommandExecutor``'s
``SandboxRequest(...)`` are the only spawn-side consumers of the sandbox
config; if either forgets the image, ``sandbox.docker_image`` silently
stops working for that entry point (a doc claim without a runnable path).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from nexuscli.config import SandboxConfig, load_config
from nexuscli.policy.os_sandbox import SandboxRequest
from nexuscli.tools.base import ToolContext
from nexuscli.tools.builtins import get_builtin_tools
from nexuscli.tools.commands import CommandExecutor
from nexuscli.tools.registry import ToolRegistry


def test_bash_tool_passes_configured_docker_image(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_wrap(command: str, **kwargs: object) -> str:
        captured.update(kwargs)
        return command

    monkeypatch.setattr("nexuscli.tools.builtins.wrap_command", fake_wrap)

    config = load_config(project_root=tmp_path)
    config.policy.hitl_mode = "never"
    config.sandbox.docker_image = "wiring-image:1"
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    context = ToolContext(cwd=str(tmp_path), config=config)

    async def run() -> None:
        bash = registry.get("bash")
        assert bash is not None
        await bash.execute({"command": "echo hi"}, context)

    asyncio.run(run())

    assert captured["docker_image"] == "wiring-image:1"


def test_executor_passes_configured_docker_image(monkeypatch) -> None:
    requests: list[SandboxRequest] = []

    class _RecordingBackend:
        name = "docker"

        def wrap(self, command: str, request: SandboxRequest) -> str:
            requests.append(request)
            return command

    monkeypatch.setattr(
        "nexuscli.tools.commands.resolve_backend", lambda _name: _RecordingBackend()
    )

    executor = CommandExecutor(
        sandbox=SandboxConfig(backend="docker", docker_image="wiring-image:2")
    )
    result = asyncio.run(executor.execute("echo hi", cwd="/tmp"))

    assert result.success
    assert requests and requests[0].docker_image == "wiring-image:2"
