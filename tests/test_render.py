from __future__ import annotations

import asyncio
from io import StringIO

from prompt_toolkit import PromptSession
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.keys import Keys
from prompt_toolkit.output import DummyOutput
from rich.cells import cell_len
from rich.console import Console

from nexuscli.config import load_config
from nexuscli.entrypoints.repl import (
    PermissionModeController,
    _bottom_toolbar,
    _permission_key_bindings,
    _prompt_message,
)
from nexuscli.render import RichRenderer
from nexuscli.render.rich_renderer import _format_tool_args


def test_banner_renders_two_line_header():
    stream = StringIO()
    console = Console(file=stream, color_system=None, width=200)
    renderer = RichRenderer(console=console)

    renderer.banner(
        model="deepseek-v4-flash",
        provider="deepseek",
        cwd="/tmp/project",
        tools=12,
        version="0.1.0",
        api_key_configured=True,
        mcp_servers=1,
        skills=3,
        agents_files=2,
        hitl_mode="never",
    )

    output = stream.getvalue()
    assert "NexusCLI" in output
    assert "v0.1.0" in output
    assert "api key ok" in output
    assert "/help commands" in output
    assert "Shift+Tab permission mode" in output
    # Every advertised key must be bound; "?" never was.
    assert "? for shortcuts" not in output
    # The old chrome is gone: no logo blocks, no what's-new panel.
    assert "███" not in output
    assert "What's new" not in output


def test_banner_flags_missing_api_key_in_red():
    stream = StringIO()
    console = Console(file=stream, color_system=None, width=200)
    renderer = RichRenderer(console=console)

    renderer.banner(
        model="deepseek-v4-flash",
        provider="deepseek",
        cwd="/tmp/project",
        tools=12,
        version="0.1.0",
        api_key_configured=False,
    )

    output = stream.getvalue()
    assert "api key missing" in output


def test_prompt_message_keeps_status_and_input_together():
    prompt = _prompt_message(
        cwd="/tmp/project",
        model="deepseek-v4-flash",
        tools=12,
        agents_files=2,
        mcp_servers=1,
        skills=3,
        stats={"total_tokens": 13187, "context_ratio": 0.013, "has_usage": True},
    )
    plain = "".join(text for _style, text in prompt)

    assert "2 AGENTS.md files" in plain
    assert "1 MCP server" in plain
    assert "3 skills · Tools 12" in plain
    assert "Default  ⇧Tab" in plain
    assert "deepseek-v4-flash" in plain
    assert "█░░░░░░░░░░░ 1%" in plain
    assert "/tmp/project" in plain
    assert plain.endswith("❯ ")


def test_permission_mode_toggle_applies_and_restores_full_access_policy(tmp_path):
    config = load_config(project_root=tmp_path)
    config.policy.hitl_mode = "always"
    controller = PermissionModeController(config)

    assert controller.mode == "default"
    assert config.policy.hitl_mode == "always"
    assert config.policy.path_guard_enabled
    assert config.policy.command_guard_enabled

    assert controller.toggle() == "auto"
    assert config.policy.hitl_mode == "never"
    assert not config.policy.path_guard_enabled
    assert not config.policy.command_guard_enabled

    assert controller.toggle() == "plan"
    assert config.policy.plan_mode
    assert config.policy.hitl_mode == "always"
    assert config.policy.path_guard_enabled
    assert config.policy.command_guard_enabled

    assert controller.toggle() == "default"
    assert not config.policy.plan_mode
    assert config.policy.hitl_mode == "always"
    assert config.policy.path_guard_enabled
    assert config.policy.command_guard_enabled


def test_shift_tab_is_bound_to_permission_mode_toggle(tmp_path):
    controller = PermissionModeController(load_config(project_root=tmp_path))
    bindings = _permission_key_bindings(controller)

    assert any(binding.keys == (Keys.BackTab,) for binding in bindings.bindings)


def test_shift_tab_input_toggles_live_permission_mode(tmp_path):
    controller = PermissionModeController(load_config(project_root=tmp_path))

    async def run_prompt() -> None:
        with create_pipe_input() as pipe_input:
            session = PromptSession(
                input=pipe_input,
                output=DummyOutput(),
                key_bindings=_permission_key_bindings(controller),
            )
            pipe_input.send_text("\x1b[Z\r")
            await session.prompt_async()

    asyncio.run(run_prompt())

    assert controller.mode == "auto"
    assert controller.config.policy.hitl_mode == "never"


def test_bottom_toolbar_uses_runtime_summary_segments():
    toolbar = _bottom_toolbar(
        "/Users/me/project",
        "deepseek-v4-flash",
        {"turns": 1, "total_tokens": 13187, "context_ratio": 0.013, "has_usage": True},
        agent=None,
    )

    assert ("class:toolbar.model", "deepseek-v4-flash") in toolbar
    assert ("class:toolbar.ctx.bar", "█░░░░░░░░░░░") in toolbar
    assert ("class:toolbar.ctx.value", "1%") in toolbar
    assert ("class:toolbar.cwd.value", "/Users/me/project") in toolbar
    assert not any(text == " TURN " for _style, text in toolbar)
    assert not any("Token" in text for _style, text in toolbar)


def test_bottom_toolbar_shows_effort_level_when_set():
    from types import SimpleNamespace

    agent = SimpleNamespace(llm_client=SimpleNamespace(reasoning_effort="high", model_name="m"))
    toolbar = _bottom_toolbar("/tmp/project", "cbcn/glm-5.3-flash", None, agent=agent)
    assert ("class:toolbar.effort", ":high") in toolbar

    unset = SimpleNamespace(llm_client=SimpleNamespace(reasoning_effort=None, model_name="m"))
    toolbar = _bottom_toolbar("/tmp/project", "cbcn/glm-5.3-flash", None, agent=unset)
    assert not any(style == "class:toolbar.effort" and text for style, text in toolbar)


def test_text_deltas_render_as_bulleted_markdown_on_turn_complete():
    stream = StringIO()
    console = Console(file=stream, color_system=None, width=120)
    renderer = RichRenderer(console=console, context_window=1000)

    renderer.handle({"type": "thinking_delta", "thinking": "需要先确认项目结构"})
    renderer.handle({"type": "text_delta", "text": "你好，我是 **Nexus"})
    renderer.handle({"type": "text_delta", "text": "CLI**\n\n- `read_file`\n- **网页搜索**"})
    renderer.handle({"type": "usage", "usage": {"input_tokens": 250, "output_tokens": 50}})
    renderer.handle({"type": "turn_complete"})
    renderer.handle({"type": "done", "total_turns": 1, "total_tokens": 300})

    output = stream.getvalue()
    assert "thinking" in output
    assert "需要先确认项目结构" in output
    assert "NexusCLI" in output
    assert "read_file" in output
    assert "网页搜索" in output
    # Panel chrome is gone: titles live in the transcript, not the stream.
    assert "Final Output" not in output
    assert "Assistant Output" not in output
    assert "Run Summary" not in output
    assert "**NexusCLI**" not in output
    assert "`read_file`" not in output

    stats = renderer.toolbar_status()
    assert stats["turns"] == 1
    assert stats["input_tokens"] == 250
    assert stats["output_tokens"] == 50
    assert stats["total_tokens"] == 300
    assert stats["context_ratio"] == 0.25
    assert stats["has_usage"] is True


def test_interleaved_thinking_does_not_repeat_assistant_marker():
    stream = StringIO()
    console = Console(file=stream, color_system=None, width=120)
    renderer = RichRenderer(console=console)

    renderer.handle({"type": "text_delta", "text": "第一段"})
    renderer.handle({"type": "thinking_delta", "thinking": "中途补充思考"})
    renderer.handle({"type": "text_delta", "text": "第二段"})
    renderer.handle({"type": "turn_complete"})

    output = stream.getvalue()
    # One flush, one speech block: the marker appears exactly once.
    assert output.count("⏺") == 1
    assert output.count("thinking") == 1
    assert "第一段" in output and "第二段" in output


def test_plan_status_and_scoped_thinking_render_with_task_identity():
    stream = StringIO()
    console = Console(file=stream, color_system=None, width=120)
    renderer = RichRenderer(console=console)

    renderer.handle({"type": "text_delta", "text": "正在规划任务"})
    renderer.handle({"type": "plan_status", "phase": "planning"})
    renderer.handle(
        {
            "type": "thinking_delta",
            "thinking": "先拆分任务",
            "phase": "planning",
        }
    )
    renderer.handle(
        {
            "type": "plan_task_started",
            "task_id": "task_1",
            "task_description": "检查模型配置",
        }
    )
    renderer.handle(
        {
            "type": "thinking_delta",
            "thinking": "读取配置文件",
            "phase": "execution",
            "task_id": "task_1",
        }
    )
    renderer.handle(
        {
            "type": "tool_call",
            "name": "read_file",
            "input": {"path": "config.py"},
            "task_id": "task_1",
        }
    )

    output = stream.getvalue()
    assert "正在规划任务" in output
    assert "thinking · planning" in output
    assert "先拆分任务" in output
    assert "Running task_1" in output
    assert "检查模型配置" in output
    assert "thinking · task_1" in output
    assert "read_file" in output
    assert "task_1" in output


def test_streaming_text_waits_for_turn_boundary_by_default():
    stream = StringIO()
    console = Console(file=stream, color_system=None, width=120, force_terminal=True)
    renderer = RichRenderer(console=console)

    renderer.handle({"type": "text_delta", "text": "chunk 1"})
    renderer.handle({"type": "text_delta", "text": "chunk 2"})

    assert stream.getvalue() == ""
    renderer.handle({"type": "turn_complete"})
    assert stream.getvalue().count("⏺") == 1


def test_tool_call_renders_as_single_compact_line():
    stream = StringIO()
    console = Console(file=stream, color_system=None, width=120)
    renderer = RichRenderer(console=console)

    renderer.handle({"type": "tool_call", "name": "list_dir", "input": {"path": "."}})
    renderer.handle(
        {
            "type": "tool_result",
            "name": "list_dir",
            "result": "README.md\nsrc/\ntests/\ndocs/",
            "is_error": False,
        }
    )

    output = stream.getvalue()
    assert "list_dir" in output
    assert "path=." in output
    assert "⎿" in output
    assert "README.md" in output
    assert "(4 lines)" in output
    # JSON payload dumps are gone.
    assert '"path"' not in output


def test_tool_error_renders_loud():
    stream = StringIO()
    console = Console(file=stream, color_system=None, width=120)
    renderer = RichRenderer(console=console)

    renderer.handle(
        {
            "type": "tool_result",
            "name": "read_file",
            "result": "file not found: nope.py",
            "is_error": True,
        }
    )

    output = stream.getvalue()
    assert "file not found: nope.py" in output
    assert "⏺" not in output  # results never carry the speech marker


def test_long_tool_output_folds_to_summary():
    stream = StringIO()
    console = Console(file=stream, color_system=None, width=120)
    renderer = RichRenderer(console=console)

    renderer.handle(
        {
            "type": "tool_result",
            "name": "read_file",
            "result": "\n".join(f"line {i}" for i in range(200)),
            "is_error": False,
        }
    )

    output = stream.getvalue()
    assert "line 0" in output
    assert "line 199" not in output
    assert "(200 lines)" in output


def test_start_run_resets_token_usage():
    stream = StringIO()
    console = Console(file=stream, color_system=None, width=120)
    renderer = RichRenderer(console=console, context_window=1000)

    renderer.handle({"type": "usage", "usage": {"input_tokens": 900, "output_tokens": 10}})
    renderer.start_run()
    renderer.handle({"type": "usage", "usage": {"input_tokens": 100, "output_tokens": 20}})
    renderer.handle({"type": "done", "total_turns": 1, "total_tokens": 120})

    stats = renderer.toolbar_status()
    assert stats["input_tokens"] == 100
    assert stats["output_tokens"] == 20
    assert stats["total_tokens"] == 120
    assert stats["context_ratio"] == 0.1


def test_missing_usage_keeps_toolbar_tokens_unavailable():
    stream = StringIO()
    console = Console(file=stream, color_system=None, width=120)
    renderer = RichRenderer(console=console, context_window=1000)

    renderer.handle({"type": "done", "total_turns": 1, "total_tokens": 0})

    toolbar = _bottom_toolbar(
        "/tmp/project", "deepseek-v4-flash", renderer.toolbar_status(), agent=None
    )
    assert ("class:toolbar.model", "deepseek-v4-flash") in toolbar
    assert ("class:toolbar.ctx.bar", "░░░░░░░░░░░░") in toolbar
    assert ("class:toolbar.ctx.value", "0%") in toolbar


def test_error_and_warning_render_with_markers():
    stream = StringIO()
    console = Console(file=stream, color_system=None, width=120)
    renderer = RichRenderer(console=console)

    renderer.handle({"type": "error", "error": "boom"})
    renderer.handle({"type": "warning", "message": "careful"})

    output = stream.getvalue()
    assert "boom" in output
    assert "careful" in output
    assert "Error:" not in output  # no legacy prefix


def test_plan_task_started_is_indent_line_not_panel():
    stream = StringIO()
    console = Console(file=stream, color_system=None, width=120)
    renderer = RichRenderer(console=console)

    renderer.handle(
        {
            "type": "plan_task_started",
            "task_id": "task_2",
            "task_description": "运行测试",
        }
    )

    output = stream.getvalue()
    assert "Running task_2" in output
    assert "运行测试" in output
    assert "Panel" not in output


def test_assistant_markdown_keeps_emphasis_and_adds_no_background():
    """Headings, bold, and inline code must survive the gutter.

    The renderer re-emits styled segments instead of flattening to strings;
    flattening silently dropped every markdown emphasis. A colored console is
    required to observe it, because ``color_system=None`` has nothing to lose.
    """
    stream = StringIO()
    console = Console(file=stream, color_system="standard", width=80, force_terminal=True)
    renderer = RichRenderer(console=console)

    renderer.handle({"type": "text_delta", "text": "## Title\n\nUse `uv sync` and **restart**.\n"})
    renderer.handle({"type": "turn_complete"})

    output = stream.getvalue()
    assert "Title" in output
    # Emphasis emitted as SGR, not stripped.
    assert "\x1b[1m" in output or "\x1b[36m" in output
    # The inline-code and fenced-code backgrounds are NOT painted: a black
    # rectangle over a light terminal is the defect this guards.
    assert "\x1b[40m" not in output
    assert "\x1b[48;" not in output


def test_tool_error_continuation_lines_do_not_repeat_the_gutter():
    stream = StringIO()
    console = Console(file=stream, color_system=None, width=40)
    renderer = RichRenderer(console=console)

    renderer.handle(
        {
            "type": "tool_result",
            "name": "bash",
            "result": "line one\nline two\nline three",
            "is_error": True,
        }
    )

    output = stream.getvalue()
    # One gizmo for the block; continuations indent under it.
    assert output.count("⎿") == 1
    assert "line one" in output
    assert "line three" in output


def test_empty_error_result_states_the_failure_instead_of_a_bare_gutter():
    stream = StringIO()
    console = Console(file=stream, color_system=None, width=80)
    renderer = RichRenderer(console=console)

    renderer.handle({"type": "tool_result", "name": "bash", "result": "", "is_error": True})

    output = stream.getvalue()
    assert "bash" in output
    assert "(empty error)" in output


def test_long_tool_arg_is_truncated_by_cell_width_not_char_count():
    """CJK is double-width; clipping by len() overshoots the budget 2x."""
    out = _format_tool_args({"path": "中" * 80})
    assert cell_len(out) <= 200
    out = _format_tool_args({"path": "a" * 400})
    assert cell_len(out) <= 200
    assert out.endswith("…")


def test_fenced_code_block_starts_on_the_marker_row():
    """The syntax block's leading pad row must not push the marker off it."""
    stream = StringIO()
    console = Console(file=stream, color_system=None, width=60)
    renderer = RichRenderer(console=console)

    renderer.handle({"type": "text_delta", "text": "```python\nx = 1\n```\n"})
    renderer.handle({"type": "turn_complete"})

    lines = [line for line in stream.getvalue().split("\n") if line]
    assert lines[0].startswith("⏺"), lines
    assert "x = 1" in lines[0]
