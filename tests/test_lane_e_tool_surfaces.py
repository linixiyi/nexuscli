"""Lane E — tool-surface & slash-command user-visible behavior (docs/testing-matrix.md).

Surfaces covered (rows the 2026-10-18 lane was assigned):
- 5.directory_tree / 5.get_file_info / 5.edit_file / 5.search_code
- 5.todo_write / 5.todo_read
- 4./hitl (approval mode switch copy)
- 4./model (no-key switch names the provider's env vars)

Each assertion pins what a *user* sees — output text, error copy, or state
change — through the same Tool objects / handlers the REPL wires up.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from nexuscli.config import load_config
from nexuscli.entrypoints.commands.model_cmds import _activate_model, _model_command
from nexuscli.entrypoints.commands.permission import _hitl_command, _permission_mode_label
from nexuscli.entrypoints.repl import PermissionModeController
from nexuscli.llm.model_profiles import DEFAULT_MODEL_PROFILES
from nexuscli.rag.code_index import CodeIndex
from nexuscli.tools import ToolRegistry, get_builtin_tools
from nexuscli.tools.base import ToolContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_context(cwd: Path) -> ToolContext:
    return ToolContext(cwd=str(cwd), config=load_config(project_root=str(cwd)))


def make_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    return registry


async def call_tool(cwd: Path, name: str, payload: dict) -> str:
    tool = make_registry().get(name)
    assert tool is not None, f"builtin tool {name} missing"
    result = await tool.execute(payload, make_context(cwd))
    return result.content


def capturing_console() -> tuple[Console, io.StringIO]:
    buffer = io.StringIO()
    return Console(file=buffer, width=200, markup=True, highlight=False), buffer


@pytest.fixture()
def sample_project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("ignored\n", encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# 5.directory_tree
# ---------------------------------------------------------------------------


def test_directory_tree_renders_readable_tree(sample_project: Path) -> None:
    out = asyncio.run(call_tool(sample_project, "directory_tree", {"path": "."}))

    assert out.startswith("directory_tree") or out.splitlines()[0].endswith("/"), out
    assert "├── " in out and "└── " in out, out  # tree connectors a human reads
    assert "src/" in out and "README.md" in out, out
    assert "app.py" in out and ".git" not in out, out  # skip-dirs stay hidden


def test_directory_tree_max_depth_and_error_copy(sample_project: Path) -> None:
    (sample_project / "src" / "deep").mkdir()
    (sample_project / "src" / "deep" / "leaf.py").write_text("x=1\n", encoding="utf-8")

    shallow = asyncio.run(call_tool(sample_project, "directory_tree", {"max_depth": 1}))
    assert "deep" not in shallow, shallow  # depth 1 shows the top level only

    deep = asyncio.run(call_tool(sample_project, "directory_tree", {"max_depth": 3}))
    assert "leaf.py" in deep, deep

    missing = asyncio.run(
        call_tool(sample_project, "directory_tree", {"path": "README.md"})
    )
    assert missing.startswith("Not a directory:"), missing


# ---------------------------------------------------------------------------
# 5.get_file_info
# ---------------------------------------------------------------------------


def test_get_file_info_reports_readable_metadata(sample_project: Path) -> None:
    target = sample_project / "README.md"
    target.write_text("demo\n", encoding="utf-8")  # refresh mtime to now
    out = asyncio.run(call_tool(sample_project, "get_file_info", {"path": "README.md"}))

    assert "Path: README.md" in out and "Type: file" in out, out
    assert "Size: 5B" in out, out
    assert "Permissions: 0o644" in out, out
    # A user asking "这个文件什么时候改的" gets a readable local timestamp;
    # the raw epoch is kept in parentheses for scripts.
    modified_line = next(line for line in out.splitlines() if line.startswith("Modified:"))
    assert "(epoch " in modified_line, out
    stamp = modified_line.removeprefix("Modified: ").split(" (epoch")[0]
    assert stamp[4] == "-" and stamp[7] == "-" and " " in stamp, stamp


def test_get_file_info_missing_path_copy(sample_project: Path) -> None:
    out = asyncio.run(call_tool(sample_project, "get_file_info", {"path": "nope.txt"}))
    assert out.startswith("Path does not exist:"), out
    assert "nope.txt" in out, out


# ---------------------------------------------------------------------------
# 5.edit_file
# ---------------------------------------------------------------------------


def test_edit_file_replaces_exactly_once_and_reports(sample_project: Path) -> None:
    (sample_project / "config.py").write_text(
        "DEBUG = False\nLEVEL = 1\nDEBUG_INFO = False\n", encoding="utf-8"
    )
    out = asyncio.run(
        call_tool(
            sample_project,
            "edit_file",
            {"path": "config.py", "old_text": "DEBUG = False", "new_text": "DEBUG = True"},
        )
    )
    text = (sample_project / "config.py").read_text(encoding="utf-8")
    assert text == "DEBUG = True\nLEVEL = 1\nDEBUG_INFO = False\n", text  # first hit only
    assert "Edited config.py" in out, out
    assert "-DEBUG = False" in out and "+DEBUG = True" in out, out  # diff summary shown

    again = asyncio.run(
        call_tool(
            sample_project,
            "edit_file",
            {"path": "config.py", "old_text": "no such line", "new_text": "x"},
        )
    )
    assert "old_text` not found" in again, again  # readable miss copy


def test_edit_file_dry_run_leaves_file_untouched(sample_project: Path) -> None:
    (sample_project / "note.txt").write_text("hello\n", encoding="utf-8")
    out = asyncio.run(
        call_tool(
            sample_project,
            "edit_file",
            {"path": "note.txt", "old_text": "hello", "new_text": "hi", "dry_run": True},
        )
    )
    assert "[DRY RUN]" in out, out
    assert (sample_project / "note.txt").read_text(encoding="utf-8") == "hello\n"


# ---------------------------------------------------------------------------
# 5.todo_write / 5.todo_read
# ---------------------------------------------------------------------------


def test_todo_write_then_read_round_trip_is_readable(sample_project: Path) -> None:
    written = asyncio.run(
        call_tool(
            sample_project,
            "todo_write",
            {
                "todos": [
                    {"content": "买牛奶", "status": "in_progress", "priority": "high"},
                    {"content": "遛狗", "status": "pending"},
                ]
            },
        )
    )
    assert "1. ▸ [in_progress] 买牛奶 (high)" in written, written
    assert "2. ☐ [pending] 遛狗" in written, written

    read_back = asyncio.run(call_tool(sample_project, "todo_read", {}))
    assert read_back == written, (read_back, written)  # REPL display == stored state
    assert (sample_project / ".nexuscli" / "todo.json").exists()  # survives restarts

    done = asyncio.run(
        call_tool(
            sample_project,
            "todo_write",
            {"todos": [{"content": "买牛奶", "status": "completed"}]},
        )
    )
    assert "☑ [completed] 买牛奶" in done, done


def test_todo_write_invalid_payload_copy(sample_project: Path) -> None:
    out = asyncio.run(
        call_tool(
            sample_project,
            "todo_write",
            {"todos": [{"content": "x", "status": "done-tomorrow"}]},
        )
    )
    assert out.startswith("Invalid todo list:"), out
    assert "status must be one of: pending, in_progress, completed" in out, out


# ---------------------------------------------------------------------------
# 5.search_code
# ---------------------------------------------------------------------------


def test_search_code_without_index_gives_index_hint(sample_project: Path) -> None:
    out = asyncio.run(call_tool(sample_project, "search_code", {"query": "VALUE"}))
    assert out == "(no indexed matches; run /index first)", out


def test_search_code_after_indexing_returns_ranked_hits(sample_project: Path) -> None:
    (sample_project / "src" / "app.py").write_text(
        "VALUE = 1\nOTHER = VALUE + 1\n", encoding="utf-8"
    )
    count = CodeIndex(str(sample_project)).rebuild()
    assert count > 0

    out = asyncio.run(call_tool(sample_project, "search_code", {"query": "VALUE"}))
    assert "src/app.py:1: VALUE = 1" in out, out
    assert "src/app.py:2: OTHER = VALUE + 1" in out, out
    assert "[hits] matched 2 line(s)" in out, out  # stats footer a user can read
    assert "terms: value" in out, out


# ---------------------------------------------------------------------------
# 4./hitl
# ---------------------------------------------------------------------------


def _hitl_fixture(tmp_path: Path):
    config = load_config(project_root=str(tmp_path))
    controller = PermissionModeController(config=config)
    return controller


def test_hitl_switches_mode_and_prints_label(tmp_path: Path) -> None:
    console, buffer = capturing_console()
    controller = _hitl_fixture(tmp_path)

    _hitl_command("auto", console, controller)
    assert controller.mode == "auto"
    assert controller.config.policy.hitl_mode == "never"  # approvals actually off
    assert "Permission mode: Auto (full access)" in buffer.getvalue(), buffer.getvalue()

    _hitl_command("default", console, controller)
    assert controller.mode == "default"
    assert controller.config.policy.hitl_mode != "never"
    assert "Permission mode: Default" in buffer.getvalue()


def test_hitl_bad_argument_shows_usage_and_keeps_mode(tmp_path: Path) -> None:
    console, buffer = capturing_console()
    controller = _hitl_fixture(tmp_path)

    _hitl_command("yolo", console, controller)
    assert "Usage: /hitl default|auto" in buffer.getvalue(), buffer.getvalue()
    assert controller.mode == "default"  # unchanged


def test_permission_mode_label_covers_plan(tmp_path: Path) -> None:
    assert _permission_mode_label("plan") == "plan (read-only)"
    assert _permission_mode_label("auto") == "Auto (full access)"
    assert _permission_mode_label("default") == "Default"


# ---------------------------------------------------------------------------
# 4./model
# ---------------------------------------------------------------------------


def _model_fixtures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for var in ("DEEPSEEK_API_KEY", "ZAI_API_KEY", "OPENAI_API_KEY", "NEXUSCLI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = load_config(project_root=str(tmp_path))
    console, buffer = capturing_console()
    renderer = SimpleNamespace(console=console, set_context_window=lambda value: None)
    agent = SimpleNamespace(
        llm_client=SimpleNamespace(
            provider_name="openai-compatible",
            model_name="base-model",
            max_context_window=128_000,
        ),
        system_prompt="old",
    )
    registry = SimpleNamespace(list_names=lambda: ["read_file"])
    return config, agent, registry, renderer, buffer


def test_activate_model_without_key_names_provider_env_vars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, agent, registry, renderer, buffer = _model_fixtures(tmp_path, monkeypatch)
    profile = next(
        item for item in DEFAULT_MODEL_PROFILES if item.provider == "deepseek"
    )

    _activate_model(profile, config, agent, registry, renderer, str(tmp_path))

    warning = buffer.getvalue()
    assert "no API key is configured for provider deepseek" in warning, warning
    assert "DEEPSEEK_API_KEY" in warning, warning  # the variable this provider reads
    assert "NEXUSCLI_API_KEY" in warning, warning  # the generic fallback
    assert agent.llm_client.model_name == "deepseek-v4-flash"  # switch itself applied


def test_model_command_argument_form_switches_and_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, agent, registry, renderer, buffer = _model_fixtures(tmp_path, monkeypatch)
    # /model prints the switch line on its own console and the no-key warning on
    # renderer.console; give both the same backing buffer.
    console = renderer.console

    asyncio.run(
        _model_command("glm glm-4.7", console, str(tmp_path), config, agent, registry, renderer)
    )

    out = buffer.getvalue()
    assert "Switched model: glm-4.7 (glm)" in out, out  # real copy: modelID + provider
    assert "no API key is configured for provider glm" in out, out
    assert "ZAI_API_KEY" in out, out
    assert agent.llm_client.model_name == "glm-4.7"
