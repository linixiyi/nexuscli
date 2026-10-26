"""loader.py — Load and validate scripted eval scenarios from JSON files.

A scenario is one JSON document describing a single offline task for the
agent::

    {
      "name": "read-task-details",
      "description": "Happy path: list tasks then fetch details",
      "prompt": "List the tasks and fetch the most urgent one.",
      "tools": [
        {"name": "list_tasks", "description": "List tracked tasks.",
         "result": "1. fix login", "is_error": false,
         "read_only": true, "requires_approval": false, "danger_level": "safe"}
      ],
      "scripted_turns": [
        {"tool_calls": [{"name": "list_tasks", "arguments": {}}]},
        {"text": "The most urgent task is 'fix login'."}
      ],
      "permission_deny": ["deploy_app"],
      "hitl_mode": "auto",
      "difficulty": "short",
      "assertions": {
        "expected_tool_sequence": ["list_tasks"],
        "final_contains": ["fix login"],
        "forbidden_tools": ["bash"],
        "max_turns": 3,
        "required_tools": ["list_tasks"],
        "max_tool_calls": {"list_tasks": 2},
        "max_total_tool_calls": 4
      }
    }

Trajectory assertions come in two flavours. ``expected_tool_sequence`` is the
exact ordered call list (precise, but brittle against real regressions);
``required_tools`` is the subset form — every listed tool must have been called
at least once, in any order. ``max_tool_calls`` caps one tool's call count
(pair it with the agent's loop guard: "the same call repeated three times must
be intercepted") and ``max_total_tool_calls`` caps the run's total call count
(the efficiency budget). Call counts include calls the loop guard intercepted:
the agent still surfaces them as ``tool_call`` events, so they measure what the
run attempted.

``difficulty`` is the author's declared tier for the scenario — one of
``short`` / ``medium`` / ``long`` (roughly: a one-or-two-step task, a
multi-step task that has to converge, an extended trajectory). It never
changes how a scenario runs or is judged; the reporter aggregates PASS/FAIL
and regression counts per tier so a regression report can show *which* tier
is degrading. The key is optional and defaults to ``medium``; when present it
must be exactly one of the three tier names (case-sensitive, no ``null``).

Validation is strict on purpose: unknown keys and wrong shapes raise
:class:`EvalSchemaError` with the file path and a human-readable reason, so a
typo in a scenario file fails loudly instead of silently weakening the run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DANGER_LEVELS = {"safe", "medium", "high"}
_HITL_MODES = {"auto", "never", "always"}
# Public (the reporter aggregates per tier in this order). A tuple, not a set:
# membership tests must not raise on an unhashable value such as ``["short"]``
# — that is a schema error to report, not a TypeError to crash on.
DIFFICULTY_LEVELS: tuple[str, ...] = ("short", "medium", "long")
DEFAULT_DIFFICULTY = "medium"
_ASSERTION_KEYS = {
    "expected_tool_sequence",
    "final_contains",
    "forbidden_tools",
    "max_turns",
    "required_tools",
    "max_tool_calls",
    "max_total_tool_calls",
}
_TOOL_KEYS = {
    "name",
    "description",
    "result",
    "is_error",
    "read_only",
    "requires_approval",
    "danger_level",
}
_TURN_KEYS = {"text", "tool_calls"}


class EvalSchemaError(ValueError):
    """Raised for malformed scenario files (CLI exits 1 with the reason)."""


@dataclass(slots=True)
class ScriptedTool:
    """A deterministic stub tool injected into the registry for one scenario."""

    name: str
    description: str
    result: str
    is_error: bool = False
    read_only: bool = True
    requires_approval: bool = False
    danger_level: str = "safe"


@dataclass(slots=True)
class ScriptedTurn:
    """One scripted LLM response: assistant text plus tool calls, in order."""

    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class EvalAssertions:
    """The seven supported per-scenario assertions (all optional).

    ``required_tools`` is the unordered subset counterpart of
    ``expected_tool_sequence``; ``max_tool_calls`` maps a tool name to its
    call-count cap and ``max_total_tool_calls`` caps the run's total.
    """

    expected_tool_sequence: list[str] | None = None
    final_contains: list[str] | None = None
    forbidden_tools: list[str] | None = None
    max_turns: int | None = None
    required_tools: list[str] | None = None
    max_tool_calls: dict[str, int] | None = None
    max_total_tool_calls: int | None = None


@dataclass(slots=True)
class EvalScenario:
    """A fully validated, ready-to-run eval scenario.

    ``difficulty`` is always one of :data:`DIFFICULTY_LEVELS` (the loader
    fills in :data:`DEFAULT_DIFFICULTY` when the file omits the key).
    """

    name: str
    prompt: str
    description: str
    tools: list[ScriptedTool]
    turns: list[ScriptedTurn]
    assertions: EvalAssertions
    permission_deny: list[str]
    hitl_mode: str
    difficulty: str
    source: str


def load_eval_scenarios(path: str | Path) -> list[EvalScenario]:
    """Load one scenario file, or every ``*.json`` file under a directory.

    Directory loading is sorted by file name so runs are deterministic. Raises
    :class:`EvalSchemaError` for unreadable files, invalid JSON, and any schema
    violation (the message always names the offending file).
    """
    root = Path(path)
    if root.is_dir():
        files = sorted(root.glob("*.json"))
        scenarios: list[EvalScenario] = []
        for file in files:
            scenarios.append(load_eval_scenario(file))
        return scenarios
    return [load_eval_scenario(root)]


def load_eval_scenario(file: str | Path) -> EvalScenario:
    """Load and validate a single scenario JSON file."""
    path = Path(file)
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvalSchemaError(f"{path}: cannot read scenario file: {exc}") from exc
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise EvalSchemaError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise EvalSchemaError(f"{path}: scenario must be a JSON object")
    return _scenario_from_mapping(data, source=str(path))


def builtin_eval_dir() -> Path | None:
    """Locate the repository's ``examples/evals`` directory, if it exists.

    Resolved relative to this source file (a source checkout) first, then
    relative to the current working directory (an installed wheel run from a
    project root). ``None`` when neither exists.
    """
    for base in (Path(__file__).resolve().parents[3], Path.cwd()):
        candidate = base / "examples" / "evals"
        if candidate.is_dir():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def _scenario_from_mapping(data: dict[str, Any], *, source: str) -> EvalScenario:
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise EvalSchemaError(f"{source}: 'name' must be a non-empty string")
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise EvalSchemaError(f"{source}: 'prompt' must be a non-empty string")
    description = data.get("description", "")
    if not isinstance(description, str):
        raise EvalSchemaError(f"{source}: 'description' must be a string when present")

    tools = _parse_tools(data.get("tools", []), source=source)
    turns = _parse_turns(data.get("scripted_turns"), source=source)
    assertions = _parse_assertions(data.get("assertions", {}), source=source)
    permission_deny = _parse_string_list(
        data.get("permission_deny", []), "'permission_deny'", source=source
    )
    hitl_mode = data.get("hitl_mode", "auto")
    if hitl_mode not in _HITL_MODES:
        raise EvalSchemaError(
            f"{source}: 'hitl_mode' must be one of {sorted(_HITL_MODES)}, got {hitl_mode!r}"
        )
    # Absent -> default tier; present means it must be exactly one of the three
    # names (``null``, ``""``, ``"Short"`` and non-strings are all rejected).
    difficulty = data.get("difficulty", DEFAULT_DIFFICULTY)
    if difficulty not in DIFFICULTY_LEVELS:
        raise EvalSchemaError(
            f"{source}: 'difficulty' must be one of {list(DIFFICULTY_LEVELS)}, got {difficulty!r}"
        )
    tool_names = {tool.name for tool in tools}
    for rule in permission_deny:
        parsed_tool = str(rule).split("(", 1)[0].strip()
        if parsed_tool and parsed_tool not in tool_names and "*" not in parsed_tool:
            raise EvalSchemaError(
                f"{source}: deny rule {rule!r} targets tool {parsed_tool!r} which the "
                "scenario does not define"
            )
    return EvalScenario(
        name=name,
        prompt=prompt,
        description=description,
        tools=tools,
        turns=turns,
        assertions=assertions,
        permission_deny=permission_deny,
        hitl_mode=hitl_mode,
        difficulty=difficulty,
        source=source,
    )


def _parse_tools(raw: Any, *, source: str) -> list[ScriptedTool]:
    if not isinstance(raw, list):
        raise EvalSchemaError(f"{source}: 'tools' must be a list when present")
    tools: list[ScriptedTool] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise EvalSchemaError(f"{source}: tools[{index}] must be an object")
        unknown = set(item) - _TOOL_KEYS
        if unknown:
            raise EvalSchemaError(f"{source}: tools[{index}] has unknown key(s): {sorted(unknown)}")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise EvalSchemaError(f"{source}: tools[{index}].name must be a non-empty string")
        if any(tool.name == name for tool in tools):
            raise EvalSchemaError(f"{source}: duplicate tool name {name!r}")
        result = item.get("result", "")
        if not isinstance(result, str):
            raise EvalSchemaError(f"{source}: tools[{index}].result must be a string")
        is_error = item.get("is_error", False)
        read_only = item.get("read_only", True)
        requires_approval = item.get("requires_approval", False)
        danger_level = item.get("danger_level", "safe")
        if not all(isinstance(flag, bool) for flag in (is_error, read_only, requires_approval)):
            raise EvalSchemaError(
                f"{source}: tools[{index}] is_error/read_only/requires_approval must be booleans"
            )
        if danger_level not in _DANGER_LEVELS:
            raise EvalSchemaError(
                f"{source}: tools[{index}].danger_level must be one of "
                f"{sorted(_DANGER_LEVELS)}, got {danger_level!r}"
            )
        tools.append(
            ScriptedTool(
                name=name,
                description=str(item.get("description", name)),
                result=result,
                is_error=is_error,
                read_only=read_only,
                requires_approval=requires_approval,
                danger_level=danger_level,
            )
        )
    return tools


def _parse_turns(raw: Any, *, source: str) -> list[ScriptedTurn]:
    if not isinstance(raw, list) or not raw:
        raise EvalSchemaError(f"{source}: 'scripted_turns' must be a non-empty list")
    turns: list[ScriptedTurn] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise EvalSchemaError(f"{source}: scripted_turns[{index}] must be an object")
        unknown = set(item) - _TURN_KEYS
        if unknown:
            raise EvalSchemaError(
                f"{source}: scripted_turns[{index}] has unknown key(s): {sorted(unknown)}"
            )
        text = item.get("text", "")
        if not isinstance(text, str):
            raise EvalSchemaError(f"{source}: scripted_turns[{index}].text must be a string")
        raw_calls = item.get("tool_calls", [])
        if not isinstance(raw_calls, list):
            raise EvalSchemaError(f"{source}: scripted_turns[{index}].tool_calls must be a list")
        tool_calls = []
        for call_index, call in enumerate(raw_calls):
            name_valid = (
                isinstance(call, dict) and isinstance(call.get("name"), str) and bool(call["name"])
            )
            if not name_valid:
                raise EvalSchemaError(
                    f"{source}: scripted_turns[{index}].tool_calls[{call_index}] must be an "
                    "object with a non-empty 'name'"
                )
            arguments = call.get("arguments", {})
            if not isinstance(arguments, dict):
                raise EvalSchemaError(
                    f"{source}: scripted_turns[{index}].tool_calls[{call_index}].arguments "
                    "must be an object"
                )
            tool_calls.append({"name": call["name"], "arguments": arguments})
        if not text and not tool_calls:
            raise EvalSchemaError(
                f"{source}: scripted_turns[{index}] must have 'text' and/or 'tool_calls'"
            )
        turns.append(ScriptedTurn(text=text, tool_calls=tool_calls))
    return turns


def _parse_assertions(raw: Any, *, source: str) -> EvalAssertions:
    if not isinstance(raw, dict):
        raise EvalSchemaError(f"{source}: 'assertions' must be an object when present")
    unknown = set(raw) - _ASSERTION_KEYS
    if unknown:
        raise EvalSchemaError(
            f"{source}: assertions has unknown key(s) {sorted(unknown)}; supported: "
            f"{sorted(_ASSERTION_KEYS)}"
        )
    expected = raw.get("expected_tool_sequence")
    if expected is not None:
        expected = _parse_string_list(expected, "'expected_tool_sequence'", source=source)
    final_contains = raw.get("final_contains")
    if final_contains is not None:
        final_contains = _parse_string_list(final_contains, "'final_contains'", source=source)
    forbidden = raw.get("forbidden_tools")
    if forbidden is not None:
        forbidden = _parse_string_list(forbidden, "'forbidden_tools'", source=source)
    max_turns = raw.get("max_turns")
    valid_turns = isinstance(max_turns, int) and not isinstance(max_turns, bool) and max_turns >= 1
    if max_turns is not None and not valid_turns:
        raise EvalSchemaError(f"{source}: assertions.max_turns must be a positive integer")
    required = raw.get("required_tools")
    if required is not None:
        required = _parse_string_list(required, "'required_tools'", source=source)
    max_tool_calls = raw.get("max_tool_calls")
    if max_tool_calls is not None:
        max_tool_calls = _parse_tool_call_caps(max_tool_calls, source=source)
    max_total = raw.get("max_total_tool_calls")
    if max_total is not None and not _is_positive_int(max_total):
        raise EvalSchemaError(
            f"{source}: assertions.max_total_tool_calls must be a positive integer, "
            f"got {max_total!r}"
        )
    return EvalAssertions(
        expected_tool_sequence=expected,
        final_contains=final_contains,
        forbidden_tools=forbidden,
        max_turns=max_turns,
        required_tools=required,
        max_tool_calls=max_tool_calls,
        max_total_tool_calls=max_total,
    )


def _parse_tool_call_caps(raw: Any, *, source: str) -> dict[str, int]:
    """Validate ``max_tool_calls``: a non-empty ``{tool name: positive int}`` map.

    An empty object asserts nothing, so it is rejected like any other typo. A
    cap of ``0`` is rejected too: "never call this tool" is what
    ``forbidden_tools`` is for.
    """
    if not isinstance(raw, dict) or not raw:
        raise EvalSchemaError(
            f"{source}: assertions.max_tool_calls must be a non-empty object mapping "
            "tool names to positive integers"
        )
    caps: dict[str, int] = {}
    for tool, limit in raw.items():
        if not isinstance(tool, str) or not tool.strip():
            raise EvalSchemaError(
                f"{source}: assertions.max_tool_calls keys must be non-empty tool names, "
                f"got {tool!r}"
            )
        if not _is_positive_int(limit):
            raise EvalSchemaError(
                f"{source}: assertions.max_tool_calls[{tool!r}] must be a positive integer, "
                f"got {limit!r}"
            )
        caps[tool] = limit
    return caps


def _is_positive_int(value: Any) -> bool:
    """True for an ``int >= 1`` that is not a ``bool`` (``true`` is not a count)."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _parse_string_list(raw: Any, label: str, *, source: str) -> list[str]:
    if isinstance(raw, str):
        return [raw]
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise EvalSchemaError(f"{source}: {label} must be a string or a list of strings")
    return list(raw)
