"""Headless（-p / serve）项目层 hooks 信任门（task-0003）。

REPL 已有项目层 hooks 的 sha256 信任门（``hooks_cmds._gate_workspace_hooks``），
但 ``-p`` 与 serve 的启动路径此前直接执行 load_config 无确认合并进来的项目层
hooks——克隆一个带 ``.nexuscli/config.json`` hooks 的仓库即可在每次工具事件上
跑任意命令。本套件钉住抽出的可复用信任门（``nexuscli.hooks.trust.
gate_project_hooks``）接入两条 headless 路径后的行为：

① ``-p`` 路径无信任不执行项目 hook；
② serve 路径同样；
③ 已信任指纹可执行（REPL 信任过的项目层在 headless 路径照常生效）；
④ 用户层 hooks 始终执行；
⑤ 无 TTY 时确认函数默认拒绝（fail-closed，不记忆）。

hook 执行断言走真实 runner 子进程（tests/test_hooks.py 的既有模式：
tmp_path 落脚本、``sys.executable`` 引用），全部离线可跑。
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import warnings
from pathlib import Path

import pytest

import nexuscli.entrypoints.cli as cli
from nexuscli.agent import QueryEngine
from nexuscli.config import load_config, user_level_hooks
from nexuscli.hooks import fire_event, trust
from nexuscli.runtime import RuntimeApiServer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeTty(io.StringIO):
    """stdin double that reports isatty() True and carries canned keystrokes."""

    def isatty(self) -> bool:
        return True


def _no_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the fail-closed branch: stdin without a TTY (as under -p/serve)."""
    monkeypatch.setattr(sys, "stdin", io.StringIO())


def _tty(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    monkeypatch.setattr(sys, "stdin", _FakeTty(answer))


def _isolate_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the user layer and the hooks trust store at *tmp_path*.

    HOME + USERPROFILE are both set (win32 ``Path.home()`` prefers
    USERPROFILE; see tests/test_hooks_trust.py) so no test reads or writes the
    real ``~/.nexuscli`` directory. The trust store constant is patched too
    because it is bound at import time, before any monkeypatching can run.
    """
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(trust, "TRUST_STORE", home / ".nexuscli" / "hooks-trust.json")
    return home


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _marker_hook(script: Path, marker: Path) -> str:
    """A hook command that leaves *marker* on disk when the runner executes it."""
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        f"from pathlib import Path\nPath(r'{marker}').write_text('fired', encoding='utf-8')\n",
        encoding="utf-8",
    )
    return f'"{sys.executable}" "{script}"'


def _hooks_section(matcher: str, command: str) -> dict[str, object]:
    return {"hooks": {"PreToolUse": [{"matcher": matcher, "hooks": [{"command": command}]}]}}


def _layered_project(
    tmp_path: Path, user_marker: Path, project_marker: Path
) -> tuple[Path, str, str]:
    """A workspace whose user layer and project layer both hook ``PreToolUse``.

    Returns ``(project_root, user_command, project_command)``; the user layer
    lands in the isolated home's ``~/.nexuscli/config.json``, the project
    layer in ``<project>/.nexuscli/config.json``.
    """
    user_command = _marker_hook(tmp_path / "user_hook.py", user_marker)
    project_command = _marker_hook(tmp_path / "project_hook.py", project_marker)
    project = tmp_path / "proj"
    user_config = tmp_path / "home" / ".nexuscli" / "config.json"
    _write_json(user_config, _hooks_section("probe", user_command))
    _write_json(project / ".nexuscli" / "config.json", _hooks_section("probe", project_command))
    return project, user_command, project_command


# ---------------------------------------------------------------------------
# -p path machinery (mirrors tests/test_cli_single_prompt.py)
# ---------------------------------------------------------------------------


_EVENTS = [
    {"type": "text_delta", "text": "ok"},
    {
        "type": "done",
        "total_turns": 1,
        "total_tokens": 1,
        "usage": {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
        "cost": {},
    },
]


class _StubEngine:
    """Scripted QueryEngine double reusing the real event aggregation helper."""

    _complete_from_events = QueryEngine._complete_from_events

    def __init__(self, **_kwargs) -> None:
        pass

    async def ask(self, message, history=None):  # noqa: ARG002
        for event in _EVENTS:
            yield event


def _install_stub_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_build_registry(config, cwd):  # noqa: ARG001
        return None, None

    monkeypatch.setattr(cli, "build_tool_registry", fake_build_registry)
    monkeypatch.setattr(cli, "create_llm_client", lambda llm, telemetry_enabled: object())
    monkeypatch.setattr(cli, "QueryEngine", lambda **kwargs: _StubEngine(**kwargs))


def _test_config(root: Path) -> object:
    config = load_config(project_root=root)
    config.llm.api_key = "unit-test-value"  # -p refuses to run without one
    return config


def _fire_pre_tool_use(config, cwd: Path) -> None:
    """Consume the gated config exactly the way the executor does."""
    outcome = asyncio.run(
        fire_event(config, "PreToolUse", {"tool_name": "probe", "tool_input": {}}, str(cwd))
    )
    assert not outcome.blocked
    assert outcome.errors == []


class _NoHooksWarning:
    """Context manager asserting no WorkspaceHooksWarning was raised inside.

    Scoped to the hooks-trust warning class only: an unrelated GC-time
    ResourceWarning (unclosed sqlite3 connection — a pre-existing repo-wide
    pattern around RuntimeApiServer's schema setup) must not fail these tests
    the way a blanket ``warnings.simplefilter("error")`` would.
    """

    def __enter__(self):
        self._cm = warnings.catch_warnings(record=True)
        self._caught = self._cm.__enter__()
        warnings.simplefilter("always")
        return self

    def __exit__(self, *exc_info) -> None:
        self._cm.__exit__(*exc_info)
        raised = [w for w in self._caught if issubclass(w.category, trust.WorkspaceHooksWarning)]
        assert not raised, f"unexpected WorkspaceHooksWarning: {raised}"


# ---------------------------------------------------------------------------
# ① -p path: untrusted project hooks must not execute
# ---------------------------------------------------------------------------


def test_prompt_path_untrusted_project_hooks_do_not_execute(tmp_path, monkeypatch) -> None:
    _isolate_home(monkeypatch, tmp_path)
    _no_tty(monkeypatch)
    user_marker = tmp_path / "user_marker.txt"
    project_marker = tmp_path / "project_marker.txt"
    project, _user_command, _project_command = _layered_project(
        tmp_path, user_marker, project_marker
    )
    config = _test_config(project)
    assert len(config.hooks.pre_tool_use) == 2  # both layers merged at load time
    _install_stub_engine(monkeypatch)

    with pytest.warns(trust.WorkspaceHooksWarning):
        asyncio.run(cli._run_prompt("hello", str(project), config))

    # Refusal strips exactly the project layer (same move as the REPL gate).
    assert config.hooks == user_level_hooks()
    # What the run's executor would fire on a tool call: user hook runs,
    # project hook does not.
    _fire_pre_tool_use(config, project)
    assert user_marker.exists()
    assert not project_marker.exists()


# ---------------------------------------------------------------------------
# ② serve path: untrusted project hooks must not execute
# ---------------------------------------------------------------------------


def test_serve_path_untrusted_project_hooks_do_not_execute(tmp_path, monkeypatch) -> None:
    _isolate_home(monkeypatch, tmp_path)
    _no_tty(monkeypatch)
    user_marker = tmp_path / "user_marker.txt"
    project_marker = tmp_path / "project_marker.txt"
    project, _user_command, _project_command = _layered_project(
        tmp_path, user_marker, project_marker
    )
    config = load_config(project_root=project)
    assert len(config.hooks.pre_tool_use) == 2

    with pytest.warns(trust.WorkspaceHooksWarning):
        server = RuntimeApiServer(cwd=str(project), config=config, api_key="unit-test-key")

    # The server's effective config carries the user layer only, so turns and
    # tasks started against it can never fire the untrusted project hook.
    assert server.config.hooks == user_level_hooks()
    _fire_pre_tool_use(server.config, Path(server.cwd))
    assert user_marker.exists()
    assert not project_marker.exists()


# ---------------------------------------------------------------------------
# ③ trusted fingerprint: project hooks may execute
# ---------------------------------------------------------------------------


def test_trusted_fingerprint_allows_project_hooks_on_prompt_path(tmp_path, monkeypatch) -> None:
    _isolate_home(monkeypatch, tmp_path)
    _no_tty(monkeypatch)
    user_marker = tmp_path / "user_marker.txt"
    project_marker = tmp_path / "project_marker.txt"
    project, _user_command, _project_command = _layered_project(
        tmp_path, user_marker, project_marker
    )
    fingerprint = trust.hooks_fingerprint(trust.workspace_hooks_section(project))
    trust.HookTrustStore().trust(str(project.resolve()), fingerprint)
    config = _test_config(project)
    _install_stub_engine(monkeypatch)

    with _NoHooksWarning():  # a trusted workspace must not warn
        asyncio.run(cli._run_prompt("hello", str(project), config))

    assert config.hooks != user_level_hooks()  # project layer kept
    _fire_pre_tool_use(config, project)
    assert project_marker.exists()  # the trusted project hook executes
    assert user_marker.exists()  # and the user layer is unaffected


def test_trusted_fingerprint_allows_project_hooks_on_serve_path(tmp_path, monkeypatch) -> None:
    _isolate_home(monkeypatch, tmp_path)
    _no_tty(monkeypatch)
    user_marker = tmp_path / "user_marker.txt"
    project_marker = tmp_path / "project_marker.txt"
    project, _user_command, _project_command = _layered_project(
        tmp_path, user_marker, project_marker
    )
    fingerprint = trust.hooks_fingerprint(trust.workspace_hooks_section(project))
    trust.HookTrustStore().trust(str(project.resolve()), fingerprint)
    config = load_config(project_root=project)

    with _NoHooksWarning():  # a trusted workspace must not warn
        server = RuntimeApiServer(cwd=str(project), config=config, api_key="unit-test-key")

    assert len(server.config.hooks.pre_tool_use) == 2
    _fire_pre_tool_use(server.config, Path(server.cwd))
    assert project_marker.exists()
    assert user_marker.exists()


# ---------------------------------------------------------------------------
# ④ user-level hooks always execute
# ---------------------------------------------------------------------------


def test_user_hooks_execute_regardless(tmp_path, monkeypatch) -> None:
    _isolate_home(monkeypatch, tmp_path)
    _no_tty(monkeypatch)
    user_marker = tmp_path / "user_marker.txt"
    user_command = _marker_hook(tmp_path / "user_hook.py", user_marker)
    user_config = tmp_path / "home" / ".nexuscli" / "config.json"
    _write_json(user_config, _hooks_section("probe", user_command))
    project = tmp_path / "proj"  # no project config.json at all
    project.mkdir()  # the hook runner spawns with cwd=<project>; it must exist
    config = _test_config(project)
    _install_stub_engine(monkeypatch)

    # No project layer -> the gate is a no-op and must not warn.
    with _NoHooksWarning():
        asyncio.run(cli._run_prompt("hello", str(project), config))

    assert [matcher.matcher for matcher in config.hooks.pre_tool_use] == ["probe"]
    _fire_pre_tool_use(config, project)
    assert user_marker.read_text(encoding="utf-8") == "fired"


# ---------------------------------------------------------------------------
# ⑤ no TTY: the default confirm refuses (fail-closed, not remembered)
# ---------------------------------------------------------------------------


def test_no_tty_default_confirm_refuses(tmp_path, monkeypatch) -> None:
    _isolate_home(monkeypatch, tmp_path)
    _no_tty(monkeypatch)
    project = tmp_path / "proj"
    _write_json(project / ".nexuscli" / "config.json", _hooks_section("probe", "echo project"))
    config = load_config(project_root=project)

    with pytest.warns(trust.WorkspaceHooksWarning):
        allowed = trust.gate_project_hooks(config, str(project))

    assert allowed is False
    assert config.hooks == user_level_hooks()
    # A refusal is never remembered: the next run faces the same gate.
    fingerprint = trust.hooks_fingerprint(trust.workspace_hooks_section(project))
    assert not trust.HookTrustStore().is_trusted(str(project.resolve()), fingerprint)


def test_tty_confirm_can_trust_once(tmp_path, monkeypatch) -> None:
    """Interactive default (TTY): a yes remembers the fingerprint like the REPL."""
    _isolate_home(monkeypatch, tmp_path)
    _tty(monkeypatch, "y\n")
    project = tmp_path / "proj"
    _write_json(project / ".nexuscli" / "config.json", _hooks_section("probe", "echo project"))
    config = load_config(project_root=project)

    with _NoHooksWarning():  # trust must not warn
        allowed = trust.gate_project_hooks(config, str(project))

    assert allowed is True
    fingerprint = trust.hooks_fingerprint(trust.workspace_hooks_section(project))
    assert trust.HookTrustStore().is_trusted(str(project.resolve()), fingerprint)
    assert [matcher.matcher for matcher in config.hooks.pre_tool_use] == ["probe"]
