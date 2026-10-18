from __future__ import annotations

import json
import re
from typing import Any

from rich.cells import cell_len
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.text import Text
from rich.theme import Theme

# Design roles (pi-style): content leads, chrome recedes. Every color is a
# foreground color — the terminal's own background stays visible, so light and
# dark themes both read. No panels, no boxes: hierarchy comes from markers,
# indentation, weight, and one accent.

# Markdown inherits two hardcoded backgrounds from rich's defaults: inline
# code ships as ``bold cyan on black`` and the default syntax theme paints
# ``#272822`` behind fenced blocks. Both would punch a black rectangle into a
# light terminal, so the renderer restates those elements foreground-only and
# uses the ANSI syntax theme, whose palette comes from the terminal itself.
_MARKDOWN_THEME = Theme(
    {
        "markdown.code": "bold cyan",
        "markdown.code_block": "none",
    }
)
_CODE_THEME = "ansi_dark"

_ACCENT = "bold cyan"  # assistant speech marker
_MUTED = "dim"  # secondary text: args, summaries, hints
_THINKING = "bright_black italic"  # model reasoning
_TOOL = "bold yellow"  # tool call marker — reads on light and dark
_OK = "green"
_ERR = "bold red"
_WARN = "yellow"

_MARKER = "⏺"  # assistant speech / primary event marker
_SUB = "⎿"  # indented sub-item: tool results, task lines

_MAX_TOOL_RESULT = 800  # chars kept inline before folding
_MAX_TOOL_ARG = 200  # chars of rendered tool args kept on one line


class RichRenderer:
    def __init__(
        self,
        console: Console | None = None,
        *,
        live_markdown: bool = False,
        context_window: int | None = None,
    ):
        self.console = console or Console()
        self._buffer: list[str] = []
        self._thinking_buffer: list[str] = []
        self._thinking_scope: str | None = None
        self._live_markdown = live_markdown
        self._live: Any = None
        self._thinking_live: Any = None
        self._context_window = context_window or 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._last_input_tokens = 0
        self._last_turns = 0
        self._last_total_tokens = 0
        self._last_context_ratio = 0.0
        self._last_has_usage = False

    def set_context_window(self, context_window: int | None) -> None:
        self._context_window = context_window or self._context_window

    def start_run(self) -> None:
        self._buffer.clear()
        self._thinking_buffer.clear()
        self._thinking_scope = None
        self._stop_live_markdown()
        self._stop_live_thinking()
        self._input_tokens = 0
        self._output_tokens = 0
        self._last_input_tokens = 0

    def reset_usage(self) -> None:
        """Forget the previous conversation's usage.

        /clear starts a new transcript, so the context bar must start empty too;
        leaving the old ratio on screen claimed the fresh session was already
        half full.
        """
        self._input_tokens = 0
        self._output_tokens = 0
        self._last_input_tokens = 0
        self._last_turns = 0
        self._last_total_tokens = 0
        self._last_context_ratio = 0.0
        self._last_has_usage = False

    def toolbar_status(self) -> dict[str, Any]:
        return {
            "turns": self._last_turns,
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "total_tokens": self._last_total_tokens,
            "context_ratio": self._last_context_ratio,
            "has_usage": self._last_has_usage,
        }

    def banner(
        self,
        *,
        model: str,
        provider: str,
        cwd: str,
        tools: int,
        version: str = "0.1.0",
        api_key_configured: bool = False,
        mcp_servers: int = 0,
        skills: int = 0,
        agents_files: int = 0,
        hitl_mode: str = "auto",
    ) -> None:
        """Two-line pi-style header: identity line, then one dim hint line.

        No logo panel, no what's-new box. Four text rows including the
        surrounding blank lines, against the old banner's twenty-plus.
        """
        _ = (model, provider, cwd, tools, mcp_servers, skills, agents_files, hitl_mode)

        self.console.print()
        head = Text()
        head.append("NexusCLI", style="bold")
        head.append(f"  v{version}", style=_MUTED)
        head.append("   ")
        if api_key_configured:
            head.append(_MARKER, style=_OK)
            head.append(" api key ok", style=_MUTED)
        else:
            head.append(_MARKER, style=_ERR)
            head.append(" api key missing", style=_ERR)
        self.console.print(head)
        self.console.print(
            Text(
                # Every hint here must be a real affordance: "?" was never
                # bound, so the old line advertised a dead key on the very
                # first screen a new user sees.
                "/help commands · Shift+Tab permission mode",
                style=_MUTED,
            )
        )
        self.console.print()

    def handle(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "text_delta":
            self._flush_thinking()
            text = str(event.get("text") or "")
            self._buffer.append(text)
            self._update_live_markdown()
        elif event_type == "thinking_delta":
            scope = _thinking_scope(event)
            if self._thinking_buffer and scope != self._thinking_scope:
                self._flush_thinking()
            self._thinking_scope = scope
            thinking = str(event.get("thinking") or "")
            self._thinking_buffer.append(thinking)
            self._update_live_thinking()
        elif event_type == "plan_status":
            self._flush_thinking()
            self._flush_markdown()
        elif event_type == "plan_task_started":
            self._flush_thinking()
            self._flush_markdown()
            task_id = str(event.get("task_id") or "task")
            description = str(event.get("task_description") or "")
            line = Text()
            line.append(f"  {_SUB} ", style=_MUTED)
            line.append(f"Running {task_id}", style="bold")
            line.append(f"  {description}", style=_MUTED)
            self.console.print(line)
        elif event_type == "usage":
            self._record_usage(event.get("usage") or {})
        elif event_type == "turn_complete":
            self._flush_thinking()
            self._flush_markdown()
        elif event_type == "tool_call":
            self._flush_thinking()
            self._flush_markdown()
            self._print_tool_call(event)
        elif event_type == "tool_result":
            self._flush_thinking()
            self._flush_markdown()
            self._print_tool_result(event)
        elif event_type == "error":
            self._flush_thinking()
            self._flush_markdown()
            self.console.print(Text(f"{_MARKER} {event.get('error') or 'error'}", style=_ERR))
        elif event_type == "warning":
            self._flush_thinking()
            self._flush_markdown()
            self.console.print(Text(f"{_MARKER} {event.get('message') or ''}", style=_WARN))
        elif event_type == "done":
            self._flush_thinking()
            self._flush_markdown()
            self._record_run_summary(event)

    def markdown(self, text: str) -> None:
        self.console.print(Markdown(text))

    def newline(self) -> None:
        self._flush_thinking()
        self._flush_markdown()
        self.console.print()

    # -- assistant output -------------------------------------------------

    def _flush_markdown(self) -> None:
        """Assistant speech as markdown under one accent marker.

        The content is the interface: no panel, no title bar. A leading
        marker lets the eye find agent speech at any scroll position; a
        trailing blank line separates turns like paragraph breaks.

        Styles survive the gutter: the block is rendered to segments and
        re-emitted with the marker as a prefix, rather than flattened to
        strings. Flattening cost every heading, bold run, and inline code
        its emphasis — the one thing markdown exists to carry.
        """
        if not self._buffer:
            return
        text = "".join(self._buffer)
        self._buffer.clear()
        self._stop_live_markdown()
        if not text.strip():
            return
        gutter = 2  # continuation lines align under the marker
        width = max(20, self.console.width - gutter)
        lines = _markdown_lines(text, width=width)
        for raw in lines:
            raw.rstrip()  # rich pads to full width; padding must not carry ink
        # Rich pads a fenced block with a blank row above and below. That was
        # a frame for the syntax-highlight background; with the background
        # gone the padding only drifts the marker off the first real row.
        while lines and not lines[0].plain:
            lines.pop(0)
        while lines and not lines[-1].plain:
            lines.pop()
        first = True
        for raw in lines:
            if raw.plain:
                line = Text()
                line.append(f"{_MARKER} " if first else " " * gutter, style="bold cyan")
                line.append_text(raw)
                self.console.print(line)
                first = False
            else:
                self.console.print()
        self.console.print()

    def _update_live_markdown(self) -> None:
        if not self._live_markdown or not self.console.is_terminal:
            return
        text = "".join(self._buffer)
        if not text.strip():
            return
        renderable = Group(Text(f"{_MARKER} ", style=_ACCENT), _markdown(text))
        if self._live is None:
            from rich.live import Live

            self._live = Live(
                renderable,
                console=self.console,
                refresh_per_second=12,
                transient=True,
                vertical_overflow="visible",
            )
            self._live.start(refresh=True)
            return
        self._live.update(renderable, refresh=True)

    def _stop_live_markdown(self) -> None:
        if self._live is None:
            return
        self._live.stop()
        self._live = None

    # -- thinking ---------------------------------------------------------

    def _flush_thinking(self) -> None:
        """Dim italic reasoning under a small label — visible but recessive."""
        if not self._thinking_buffer:
            return
        text = "".join(self._thinking_buffer)
        self._thinking_buffer.clear()
        scope = self._thinking_scope
        self._thinking_scope = None
        self._stop_live_thinking()
        if not text.strip():
            return
        label = f"thinking · {scope}" if scope else "thinking"
        self.console.print(Text(f"  {label}", style=_MUTED))
        width = max(20, self.console.width - 4)
        for raw in _render_lines(Text(text.rstrip(), style=_THINKING), width=width):
            if raw:
                self.console.print(Text(f"  {raw}", style=_THINKING))
        self.console.print()

    def _update_live_thinking(self) -> None:
        if not self.console.is_terminal:
            return
        text = "".join(self._thinking_buffer)
        if not text.strip():
            return
        scope = self._thinking_scope
        label = f"  {f'thinking · {scope}' if scope else 'thinking'}…"
        renderable = Group(Text(label, style=_MUTED), Text(text[-2000:], style=_THINKING))
        if self._thinking_live is None:
            from rich.live import Live

            self._thinking_live = Live(
                renderable,
                console=self.console,
                refresh_per_second=12,
                transient=True,
                vertical_overflow="visible",
            )
            self._thinking_live.start(refresh=True)
            return
        self._thinking_live.update(renderable, refresh=True)

    def _stop_live_thinking(self) -> None:
        if self._thinking_live is None:
            return
        self._thinking_live.stop()
        self._thinking_live = None

    # -- usage ------------------------------------------------------------

    def _record_usage(self, usage: dict[str, Any]) -> None:
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        self._input_tokens += input_tokens
        self._output_tokens += output_tokens
        if input_tokens:
            self._last_input_tokens = input_tokens

    def _record_run_summary(self, event: dict[str, Any]) -> None:
        total_tokens = int(event.get("total_tokens") or self._input_tokens + self._output_tokens)
        turns = int(event.get("total_turns") or 0)
        has_usage = total_tokens > 0 or self._input_tokens > 0 or self._output_tokens > 0
        context_ratio = (
            self._last_input_tokens / self._context_window if self._context_window > 0 else 0
        )
        self._last_turns = turns
        self._last_total_tokens = total_tokens
        self._last_context_ratio = context_ratio
        self._last_has_usage = has_usage

    # -- tools ------------------------------------------------------------

    def _print_tool_call(self, event: dict[str, Any]) -> None:
        """One line per tool call: `⏺ name(args)` — the compact single-line form.

        JSON payloads collapse to `key=value` fragments sized to fit; the
        full payload belongs to the transcript, not the visual stream.
        """
        name = str(event.get("name") or "unknown")
        payload = event.get("input") or {}
        args = _format_tool_args(payload)
        task_id = str(event.get("task_id") or "").strip()
        label = f"{name} · {task_id}" if task_id else name
        line = Text()
        line.append(f"{_MARKER} ", style=_TOOL)
        line.append(label, style="bold")
        if args:
            line.append(f"({args})", style=_MUTED)
        self.console.print(line)

    def _print_tool_result(self, event: dict[str, Any]) -> None:
        """Result lines indented under the call: errors loud, success quiet."""
        is_error = bool(event.get("is_error"))
        name = str(event.get("name") or "unknown")
        result = str(event.get("result") or "")
        task_id = str(event.get("task_id") or "").strip()
        label = f"{name} · {task_id}" if task_id else name
        if is_error:
            body = result[:_MAX_TOOL_RESULT]
            if len(result) > _MAX_TOOL_RESULT:
                body += "…"
            if not body.strip():
                self.console.print(
                    Text(f"  {_SUB} ", style=_ERR).append(f"{label}: (empty error)", style=_ERR)
                )
                return
            # The first line carries the label and gutter; continuation lines
            # indent under it instead of repeating the gizmo on every row.
            indent = " " * 4
            # Wrap to the indent-adjusted width so re-indenting continuation
            # lines cannot push a row past the console edge.
            avail = max(20, self.console.width - len(indent))
            text = Text(f"  {_SUB} ", style=_ERR)
            text.append(f"{label}: ", style=_ERR)
            text.append(body, style=_ERR)
            wrapped = text.wrap(self.console, avail, overflow="fold") or [Text("")]
            for index, raw in enumerate(wrapped):
                plain = raw.plain.rstrip()
                if index == 0:
                    self.console.print(Text(plain, style=raw.style))
                elif plain:
                    self.console.print(Text(indent + plain, style=raw.style))
                else:
                    self.console.print()
            return
        # Success: one quiet summary line. The full output stays in the
        # transcript and the model's context; the screen shows outcome only.
        split = result.splitlines()
        first_line = split[0].strip() if split else "(empty result)"
        summary = first_line if len(split) <= 1 else f"{first_line}  ({len(split)} lines)"
        line = Text(f"  {_SUB} ", style=_MUTED)
        line.append(f"{label}: ", style=_MUTED)
        line.append(summary, style=_MUTED)
        # One row, always: a wrapped summary would silently double the
        # screen cost of the cheapest event in the stream.
        line.truncate(max(20, self.console.width), overflow="ellipsis")
        self.console.print(line)


def _format_tool_args(payload: Any) -> str:
    """key=value fragments, each and the whole line truncated to fit."""
    if not isinstance(payload, dict) or not payload:
        return ""
    fragments: list[str] = []
    for key, value in payload.items():
        if isinstance(value, str):
            rendered = _truncate_cells(value, 60)
        else:
            try:
                rendered = json.dumps(value, ensure_ascii=False)
            except TypeError:
                rendered = str(value)
            rendered = _truncate_cells(rendered, 60)
        fragments.append(f"{key}={rendered}")
    joined = ", ".join(fragments)
    return _truncate_cells(joined, _MAX_TOOL_ARG)


def _truncate_cells(text: str, limit: int) -> str:
    """Clip to ``limit`` terminal cells, not code points.

    CJK and emoji occupy two cells each, so slicing by ``len`` overshoots the
    budget by up to 2x and pushes an otherwise single-line tool call onto a
    second row. Walks cell widths so double-width glyphs are never split.
    """
    if cell_len(text) <= limit:
        return text
    budget = limit - 1  # room for the ellipsis
    used = 0
    keep: list[str] = []
    for char in text:
        size = cell_len(char)
        if used + size > budget:
            break
        used += size
        keep.append(char)
    return "".join(keep) + "…"


_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_BLOCK_PREFIXES = ("#", ">", "|", "    ", "\t")
_LIST_ITEM = re.compile(r"^\s*([-*+]|\d+[.)])\s")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}")


def _preserve_line_breaks(text: str) -> str:
    """Keep ordinary line breaks in assistant speech.

    Markdown treats a single newline as a soft break and renders the two
    lines as one paragraph, so a reply containing a log excerpt, a numbered
    walkthrough, or a diff arrived on screen as one wall of text: the model
    sent 205 lines, the terminal showed 1 (measured through the renderer with
    ``a\\nb\\nc`` -> ``a b c``). Single-prompt mode never showed this because
    it streams raw text; only the Rich path collapsed.

    Every line that a human meant as its own line gets a CommonMark hard
    break (two trailing spaces) appended, except where a break is already
    structural or would corrupt the block: blank lines, fenced code,
    headings, list items, table rows, block quotes, indented code, and lines
    already ending in a hard break.
    """
    lines = text.split("\n")
    out: list[str] = []
    in_fence = False
    for index, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        stripped = line.strip()
        structural = (
            not stripped
            or not next_line.strip()
            or line.startswith(_BLOCK_PREFIXES)
            or bool(_LIST_ITEM.match(line))
            or bool(_TABLE_RULE.match(line))
            or line.rstrip().endswith("  ")
            or line.rstrip().endswith("\\")
        )
        if structural:
            out.append(line)
        else:
            out.append(line.rstrip() + "  ")
    return "\n".join(out)


def _markdown(text: str) -> Markdown:
    """Markdown with a foreground-only palette that follows the terminal theme."""
    return Markdown(
        _preserve_line_breaks(text),
        code_theme=_CODE_THEME,
        inline_code_theme=_CODE_THEME,
    )


def _markdown_lines(text: str, *, width: int) -> list[Text]:
    """Styled markdown lines, wrapped to ``width``, backgrounds stripped.

    Rendering through a scratch console gives back one ``Text`` per visual
    line, so the caller can prepend a gutter without re-wrapping or losing
    annotations.
    """
    scratch = Console(
        width=width,
        color_system=None,
        highlight=False,
        emoji=False,
        legacy_windows=False,
        no_color=True,
        theme=_MARKDOWN_THEME,
    )
    lines: list[Text] = []
    for segments in scratch.render_lines(_markdown(text), scratch.options):
        line = Text()
        for segment in segments:
            line.append(segment.text, style=segment.style)
        lines.append(line)
    return lines


def _thinking_scope(event: dict[str, Any]) -> str | None:
    task_id = str(event.get("task_id") or "").strip()
    if task_id:
        return task_id
    if event.get("phase") == "planning":
        return "planning"
    return None


def _render_lines(renderable: Any, *, width: int) -> list[str]:
    """Render a Rich object into plain text lines without emitting ANSI."""
    scratch = Console(
        width=width,
        color_system=None,
        highlight=False,
        emoji=False,
        legacy_windows=False,
        no_color=True,
    )
    with scratch.capture() as capture:
        scratch.print(renderable)
    return capture.get().rstrip("\n").split("\n")
