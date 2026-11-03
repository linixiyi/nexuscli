"""Tests for the real track's controlled write sandbox and quality gate.

``sandbox_write: true`` (spec 2026-11-03_add-real-sandbox-write-quality) opens
ONE scenario's write channel: ``write_file`` / ``edit_file`` execute against a
fresh one-shot sandbox directory, the PathGuard root is pinned to that sandbox
(the Agent runs with the sandbox as ``cwd``, which is what the file tools'
guard is built from), ``bash`` stays deny-ruled, every call is audited, and a
ruff + py_compile quality gate records the run's ``quality`` block.

Everything here stays hermetic in the AGENTS.md sense: the LLM is the injected
fake client, no network call, no repo write. The sandbox itself is REAL disk
inside the system temp directory — that is the point: the write channel must
be exercised for real, and the sandbox *is* a throwaway temp directory. Runs
remove it again; the one test that forces a cleanup failure removes the
leftover itself.

Covers:
- the loader matrix: ``sandbox_write`` typing, both quality assertion keys
  (non-negative cap, bool), the ``sandbox_write`` dependency of the quality
  keys, and the hermetic track rejecting every new key as unknown;
- the main-whitelist invariant: ``REAL_TRACK_TOOLS`` untouched, the sandbox
  set is a separate constant, the per-scenario config variant opens only the
  write pair (user spec'd deny rules still win, ``bash`` stays denied) and a
  non-sandbox scenario still denies writes fail-closed;
- sandbox confinement: writes inside the sandbox succeed (audited as allow),
  repository-body writes and ``../`` traversal are rejected (audited), bash
  is denied, and the honest boundary test: with PathGuard monkeypatched
  permissive the write escapes — the guard IS the confinement mechanism;
- the quality gate pure function: clean file, violation counting, compile
  error, missing-ruff degradation to null (both the find_spec short-circuit
  and the unspawnable-interpreter branch);
- the assertion sides, fail-closed: cap exceeded fails, a null ruff metric
  fails a declared cap with install guidance, compile dirt fails
  ``require_compile_clean``, no quality keys means record-only (never judged),
  cap 0 passes a clean write;
- cleanup: success leaves no ``nexuscli-eval-write-*`` residue, a forced
  ``rmtree`` failure emits exactly ONE :class:`SandboxCleanupWarning` per
  process and never raises;
- the report: the ``quality`` block joins each run entry additively (legacy
  keys all-equal, ``null`` for read-only runs).
"""

from __future__ import annotations

import copy
import json
import shutil
import tempfile
import warnings
from pathlib import Path

import pytest

import nexuscli.eval.real as real_module
from nexuscli.eval import (
    REAL_TRACK_TOOLS,
    EvalSchemaError,
    builtin_real_eval_dir,
    load_eval_scenario,
    load_real_eval_scenario,
)
from nexuscli.eval.real import (
    REAL_SANDBOX_WRITE_TOOLS,
    SANDBOX_DIR_PREFIX,
    SandboxCleanupWarning,
    _apply_sandbox_write_config,
    _load_real_config,
    _quality_assertion_failures,
    build_real_report,
    run_quality_gate,
    run_real_scenarios,
)
from nexuscli.policy import evaluate_permissions
from nexuscli.tools import file_ops as fops_module
from nexuscli.tools.builtins import get_builtin_tools

# The clean, spec-shaped module the sandbox scenarios write (same content the
# shipped example scenario 04 asks for): compiles, zero default-ruff rules
# selected (ruff's default select is E4/E7/E9/F).
_CLEAN_GREETING = (
    "def build_greeting(name):\n"
    '    greeting = f"Hello, {name}!"\n'
    "    return greeting\n"
    "\n"
    "\n"
    'print(build_greeting("nexuscli"))\n'
)
# One unused import: a default-selected F401 violation, deterministic with the
# stock ruff rule set (the sandbox lives outside the repo, so no pyproject
# config can sneak in a different selection).
_DIRTY_IMPORT = "import os\n"
_BROKEN_SYNTAX = "def build_greeting(:\n"

_MARKER = "SANDBOX-WRITE-OK"


# ---------------------------------------------------------------------------
# Fake LLM (the injection seam) + scenario/run helpers
# ---------------------------------------------------------------------------


class _FakeSandboxLlm:
    """Scripted stand-in speaking the real streaming protocol (no cost calc)."""

    model_name = "fake-sandbox-model"
    provider_name = "fake-sandbox"
    max_context_window = 100_000

    def __init__(self, turns: list[dict]) -> None:
        self._turns = list(turns)
        self._index = 0
        self.seen_messages: list[list[str]] = []

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ANN001, ARG002
        self.seen_messages.append([str(getattr(message, "content", "")) for message in messages])
        if self._index >= len(self._turns):
            yield {"type": "message_end", "stop_reason": "end_turn"}
            return
        turn = self._turns[self._index]
        self._index += 1
        for index, call in enumerate(turn.get("tool_calls", [])):
            yield {
                "type": "tool_call_delta",
                "tool_call": {
                    "index": index,
                    "id": f"call_{self._index}_{index}",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(call.get("arguments") or {}),
                    },
                },
            }
        if turn.get("text"):
            yield {"type": "text_delta", "text": turn["text"]}
        yield {"type": "usage", "usage": {"input_tokens": 10, "output_tokens": 5}}
        yield {
            "type": "message_end",
            "stop_reason": "tool_use" if turn.get("tool_calls") else "end_turn",
        }


def _tool_turn(name: str, arguments: dict) -> dict:
    return {"tool_calls": [{"name": name, "arguments": arguments}]}


def _text_turn(text: str) -> dict:
    return {"text": text}


def _write_scenario(directory: Path, data: dict, filename: str = "scenario.json") -> Path:
    path = directory / filename
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _sandbox_scenario_data(**assertions) -> dict:
    """A sandbox_write scenario body; assertion kwargs replace the defaults.

    ``None``-valued kwargs REMOVE the key (present-but-null is a schema error
    by design — "not asserted" is spelled by absence).
    """
    base = {
        "max_turns": 6,
        "max_ruff_violations": 0,
        "require_compile_clean": True,
        "final_contains": [_MARKER],
    }
    for key, value in assertions.items():
        if value is None:
            base.pop(key, None)
        else:
            base[key] = value
    return {
        "name": "real-sandbox-write-smoke",
        "prompt": "Create greeting.py in the workspace, then confirm.",
        "sandbox_write": True,
        "assertions": base,
    }


def _run(scenario_path: Path, *, root: Path, turns: list[dict]):
    scenario = load_real_eval_scenario(scenario_path)
    clients: list[_FakeSandboxLlm] = []

    def factory(_llm_config):  # noqa: ANN001, ANN202
        client = _FakeSandboxLlm(turns)
        clients.append(client)
        return client

    results = run_real_scenarios(
        [scenario],
        root=str(root),
        llm_client_factory=factory,
    )
    return results, clients


def _leaked_sandboxes() -> list[str]:
    return [entry.name for entry in Path(tempfile.gettempdir()).glob(f"{SANDBOX_DIR_PREFIX}*")]


def _audit_events(user_home: Path) -> list[dict]:
    audit_path = user_home / ".nexuscli" / "audit.jsonl"
    if not audit_path.is_file():
        return []
    return [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]


def _followup_contents(clients: list[_FakeSandboxLlm]) -> list[str]:
    """Everything the model saw on the call after its first (tool feedback)."""
    return list(clients[0].seen_messages[1])


@pytest.fixture
def user_home(_isolate_user_home) -> Path:
    """The throwaway user home the autouse conftest fixture created."""
    return _isolate_user_home


# ---------------------------------------------------------------------------
# Loader — the sandbox_write / quality-assertion schema matrix
# ---------------------------------------------------------------------------


def test_sandbox_write_defaults_to_false_and_parses(tmp_path):
    path = _write_scenario(
        tmp_path,
        {
            "name": "plain",
            "prompt": "read-only prompt",
            "assertions": {"max_turns": 4},
        },
    )

    scenario = load_real_eval_scenario(path)

    assert scenario.sandbox_write is False, "absent key means the unchanged read-only track"
    assert scenario.assertions.max_ruff_violations is None
    assert scenario.assertions.require_compile_clean is None


def test_sandbox_write_true_with_quality_assertions_parses(tmp_path):
    path = _write_scenario(tmp_path, _sandbox_scenario_data())

    scenario = load_real_eval_scenario(path)

    assert scenario.sandbox_write is True
    assert scenario.assertions.max_ruff_violations == 0, "0 is a legal cap, not 'absent'"
    assert scenario.assertions.require_compile_clean is True


@pytest.mark.parametrize("bad", ["yes", 1, None, [], {"sandbox_write": True}])
def test_sandbox_write_must_be_a_boolean(tmp_path, bad):
    data = _sandbox_scenario_data()
    data["sandbox_write"] = bad
    path = _write_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_real_eval_scenario(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "sandbox_write" in message
    assert "boolean" in message


@pytest.mark.parametrize("bad", [-1, "0", True, 1.5])
def test_max_ruff_violations_must_be_a_non_negative_int(tmp_path, bad):
    data = _sandbox_scenario_data(max_ruff_violations=bad)
    path = _write_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_real_eval_scenario(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "max_ruff_violations" in message
    assert "non-negative" in message


def test_max_ruff_violations_null_is_rejected_not_treated_as_absent(tmp_path):
    """Present-but-null is a wrong shape like any other (max_rounds strictness)."""
    data = _sandbox_scenario_data()
    data["assertions"]["max_ruff_violations"] = None
    path = _write_scenario(tmp_path, data, filename="null-cap.json")

    with pytest.raises(EvalSchemaError) as excinfo:
        load_real_eval_scenario(path)

    assert "max_ruff_violations" in str(excinfo.value)


@pytest.mark.parametrize("bad", ["yes", 1])
def test_require_compile_clean_must_be_a_boolean(tmp_path, bad):
    data = _sandbox_scenario_data(require_compile_clean=bad)
    path = _write_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_real_eval_scenario(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "require_compile_clean" in message
    assert "boolean" in message


def test_require_compile_clean_null_is_rejected_not_treated_as_absent(tmp_path):
    data = _sandbox_scenario_data()
    data["assertions"]["require_compile_clean"] = None
    path = _write_scenario(tmp_path, data, filename="null-compile.json")

    with pytest.raises(EvalSchemaError) as excinfo:
        load_real_eval_scenario(path)

    assert "require_compile_clean" in str(excinfo.value)


def test_quality_assertion_without_sandbox_write_is_rejected(tmp_path):
    """The gate never runs without the sandbox; the combination is a load-time bug."""
    data = _sandbox_scenario_data()
    data["sandbox_write"] = False
    path = _write_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_real_eval_scenario(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "sandbox_write" in message
    assert "max_ruff_violations" in message
    assert "require_compile_clean" in message

    # The compile-clean key alone triggers the same dependency error.
    data2 = _sandbox_scenario_data()
    data2["sandbox_write"] = False
    data2["assertions"].pop("max_ruff_violations")
    path2 = _write_scenario(tmp_path, data2, filename="compile-only.json")
    with pytest.raises(EvalSchemaError) as excinfo2:
        load_real_eval_scenario(path2)
    assert "require_compile_clean" in str(excinfo2.value)


def test_hermetic_track_rejects_sandbox_write_key(tmp_path):
    data = {
        "name": "hermetic",
        "prompt": "p",
        "scripted_turns": [{"text": "ok"}],
        "sandbox_write": True,
    }
    path = _write_scenario(tmp_path, data, filename="hermetic.json")

    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)

    message = str(excinfo.value)
    assert "sandbox_write" in message
    assert "unknown scenario key(s)" in message


@pytest.mark.parametrize("quality_key", ["max_ruff_violations", "require_compile_clean"])
def test_hermetic_track_rejects_quality_assertion_keys(tmp_path, quality_key):
    data = {
        "name": "hermetic",
        "prompt": "p",
        "scripted_turns": [{"text": "ok"}],
        "assertions": {quality_key: 0 if quality_key == "max_ruff_violations" else True},
    }
    path = _write_scenario(tmp_path, data, filename=f"hermetic-{quality_key}.json")

    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)

    message = str(excinfo.value)
    assert quality_key in message
    assert "assertions has unknown key(s)" in message


# ---------------------------------------------------------------------------
# Main whitelist invariant + the per-scenario config variant
# ---------------------------------------------------------------------------


def test_main_whitelist_untouched_by_the_sandbox_extension():
    """REAL_TRACK_TOOLS keeps its exact 14 read-only members; the write pair
    lives in a SEPARATE constant so the main whitelist's semantics never blur."""
    assert sorted(REAL_TRACK_TOOLS) == [
        "directory_tree",
        "get_file_info",
        "glob",
        "glob_files",
        "grep",
        "grep_code",
        "list_dir",
        "load_skill",
        "read_file",
        "search_code",
        "task_output",
        "todo_read",
        "web_fetch",
        "web_search",
    ]
    assert sorted(REAL_SANDBOX_WRITE_TOOLS) == ["edit_file", "write_file"]
    assert not (REAL_TRACK_TOOLS & REAL_SANDBOX_WRITE_TOOLS)
    assert "bash" not in REAL_SANDBOX_WRITE_TOOLS, "the sandbox opens writes, never execution"


def test_sandbox_config_variant_opens_only_the_write_pair(tmp_path):
    config = _load_real_config(str(tmp_path))
    assert {"write_file", "edit_file", "bash", "todo_write"} <= set(config.permissions.deny)

    sandbox_config = copy.deepcopy(config)
    _apply_sandbox_write_config(sandbox_config)

    assert "write_file" not in sandbox_config.permissions.deny
    assert "edit_file" not in sandbox_config.permissions.deny
    # Execution and state-writing channels stay denied in the sandbox scenario.
    assert {"bash", "execute_command", "task", "todo_write", "search_memory"} <= set(
        sandbox_config.permissions.deny
    )
    assert {"write_file", "edit_file"} <= set(sandbox_config.permissions.allow)
    # The shared track config was never touched (the runner passes a copy).
    assert "write_file" in config.permissions.deny
    assert "write_file" not in config.permissions.allow


def test_sandbox_config_variant_user_scoped_deny_rule_still_wins(tmp_path):
    """Only the track's own bare-name denies are lifted; a spec'd user deny rule
    keeps denying (deny still beats allow, even inside a sandbox scenario)."""
    config = _load_real_config(str(tmp_path))
    config.permissions.deny = [*config.permissions.deny, "write_file(src/**)"]

    sandbox_config = copy.deepcopy(config)
    _apply_sandbox_write_config(sandbox_config)

    assert "write_file(src/**)" in sandbox_config.permissions.deny
    decision = evaluate_permissions(
        sandbox_config.permissions, "write_file", {"path": "src/leak.txt"}
    )
    assert decision is not None and decision.action == "deny"
    # A sandbox-internal path is not covered by the spec'd rule: the write channel works.
    inside = evaluate_permissions(sandbox_config.permissions, "write_file", {"path": "demo.py"})
    assert inside is not None and inside.action == "allow"


def test_track_pins_path_guard_against_user_config(user_home, tmp_path):
    """Security-review fix: the confinement promise must not depend on user policy.

    The file tools build their PathGuard from ``policy.path_guard_enabled``
    (tools/file_ops.resolve_path), and the sandbox branch allow-rules the
    write pair into executor auto-approve — so a user config switching the
    guard off would let an unattended sandbox run write anywhere. The track
    pins the guard on like audit/hitl/plan_mode.
    """
    (user_home / ".nexuscli").mkdir(parents=True, exist_ok=True)
    (user_home / ".nexuscli" / "config.json").write_text(
        json.dumps({"policy": {"path_guard_enabled": False}}), encoding="utf-8"
    )

    config = _load_real_config(str(tmp_path))

    assert config.policy.path_guard_enabled is True, "the track pin must win over user config"


def test_sandbox_confinement_survives_user_path_guard_disabled(user_home, tmp_path):
    """End to end: with the guard disabled in user config, an absolute-path
    write at the scenario root is STILL rejected and audited."""
    (user_home / ".nexuscli").mkdir(parents=True, exist_ok=True)
    (user_home / ".nexuscli" / "config.json").write_text(
        json.dumps({"policy": {"path_guard_enabled": False}}), encoding="utf-8"
    )
    scenario_path = _write_scenario(
        tmp_path,
        _sandbox_scenario_data(max_ruff_violations=None, require_compile_clean=None),
        filename="guard-off-user-config.json",
    )

    results, clients = _run(
        scenario_path,
        root=tmp_path,
        turns=[
            _tool_turn("write_file", {"path": str(tmp_path / "evil.txt"), "content": "nope"}),
            _text_turn("could not write"),
        ],
    )

    assert not (tmp_path / "evil.txt").exists(), "the repository body must stay unwritable"
    assert any("path escapes workspace" in content for content in _followup_contents(clients))
    write_events = [
        event for event in _audit_events(user_home) if event["tool_name"] == "write_file"
    ]
    assert write_events, "the rejected escape must leave an audit record"
    assert write_events[-1]["outcome"] == "error"


def test_non_sandbox_scenario_still_denies_writes_fail_closed(user_home, tmp_path):
    """Zero regression: without sandbox_write the write pair is deny-ruled and
    audited exactly as before (the sandbox branch never opens)."""
    scenario_path = _write_scenario(
        tmp_path,
        {
            "name": "still-readonly",
            "prompt": "Write a file.",
            "assertions": {"max_turns": 4},
        },
    )

    results, clients = _run(
        scenario_path,
        root=tmp_path,
        turns=[
            _tool_turn("write_file", {"path": "evil.txt", "content": "nope"}),
            _text_turn("understood"),
        ],
    )

    result = results[0]
    assert not (tmp_path / "evil.txt").exists()
    assert result.quality is None, "read-only runs record no quality block"
    assert any("denied by permission rule" in content for content in _followup_contents(clients))
    write_events = [
        event for event in _audit_events(user_home) if event["tool_name"] == "write_file"
    ]
    assert write_events and write_events[-1]["outcome"] == "deny"


# ---------------------------------------------------------------------------
# Sandbox confinement
# ---------------------------------------------------------------------------


def test_sandbox_write_executes_inside_sandbox_audited_and_cleaned(user_home, tmp_path):
    scenario_path = _write_scenario(tmp_path, _sandbox_scenario_data())
    residue_before = _leaked_sandboxes()

    results, clients = _run(
        scenario_path,
        root=tmp_path,
        turns=[
            _tool_turn("write_file", {"path": "greeting.py", "content": _CLEAN_GREETING}),
            _text_turn(f"{_MARKER} greeting.py written"),
        ],
    )

    result = results[0]
    assert result.passed, result.failure_reasons
    assert result.tool_sequence == ["write_file"]
    # The real handler ran against the sandbox: its success text reached the model…
    assert any("Wrote greeting.py" in content for content in _followup_contents(clients))
    # …and the file exists NOWHERE in the scenario root (the repository body).
    assert not (tmp_path / "greeting.py").exists()
    # The quality block saw the file inside the sandbox, clean on both metrics.
    assert result.quality == {
        "files_written": ["greeting.py"],
        "ruff_violations": 0,
        "compile_ok": True,
        "ruff_note": None,
    }
    # Audited as an allow through the standing rule (approver permission-rule).
    write_events = [
        event for event in _audit_events(user_home) if event["tool_name"] == "write_file"
    ]
    assert write_events and write_events[-1]["outcome"] == "allow"
    assert write_events[-1]["approver"] == "permission-rule"
    # The sandbox is gone after the run.
    assert _leaked_sandboxes() == residue_before


def test_repo_body_write_is_rejected_and_audited(user_home, tmp_path):
    """An absolute path at the scenario root (= the repository body here) must
    not be writable: PathPolicyError -> error tool result -> audited trail."""
    scenario_path = _write_scenario(
        tmp_path,
        _sandbox_scenario_data(max_ruff_violations=None, require_compile_clean=None),
        filename="escape-abs.json",
    )

    results, clients = _run(
        scenario_path,
        root=tmp_path,
        turns=[
            _tool_turn("write_file", {"path": str(tmp_path / "evil.txt"), "content": "nope"}),
            _text_turn("could not write"),
        ],
    )

    result = results[0]
    assert not (tmp_path / "evil.txt").exists(), "the repository body must stay unwritable"
    assert any("path escapes workspace" in content for content in _followup_contents(clients))
    write_events = [
        event for event in _audit_events(user_home) if event["tool_name"] == "write_file"
    ]
    assert write_events, "the rejected escape must leave an audit record"
    assert write_events[-1]["outcome"] == "error"
    assert result.quality is not None, "the gate still ran over the (empty) sandbox"


def test_traversal_escape_is_rejected_and_audited(user_home, tmp_path):
    scenario_path = _write_scenario(
        tmp_path,
        _sandbox_scenario_data(max_ruff_violations=None, require_compile_clean=None),
        filename="escape-rel.json",
    )

    results, clients = _run(
        scenario_path,
        root=tmp_path,
        turns=[
            _tool_turn("write_file", {"path": "../outside.txt", "content": "nope"}),
            _text_turn("could not write"),
        ],
    )

    assert not (tmp_path / "outside.txt").exists()
    assert any("path escapes workspace" in content for content in _followup_contents(clients))
    write_events = [
        event for event in _audit_events(user_home) if event["tool_name"] == "write_file"
    ]
    assert write_events and write_events[-1]["outcome"] == "error"


def test_bash_stays_denied_inside_sandbox_scenario(user_home, tmp_path):
    """The sandbox opens FILE WRITES only: bash is deny-ruled and audited even
    in a sandbox_write scenario, and nothing gets executed."""
    scenario_path = _write_scenario(
        tmp_path,
        _sandbox_scenario_data(max_ruff_violations=None, require_compile_clean=None),
        filename="bash.json",
    )

    results, clients = _run(
        scenario_path,
        root=tmp_path,
        turns=[
            _tool_turn("bash", {"command": "echo pwned > pwn.txt"}),
            _text_turn("command not available"),
        ],
    )

    result = results[0]
    assert "bash" in result.tool_sequence
    assert any("denied by permission rule" in content for content in _followup_contents(clients))
    assert not (tmp_path / "pwn.txt").exists()
    bash_events = [event for event in _audit_events(user_home) if event["tool_name"] == "bash"]
    assert bash_events and bash_events[-1]["outcome"] == "deny"
    assert bash_events[-1]["approver"] == "permission-rule"


def test_write_escape_with_path_guard_disabled_documents_the_boundary(tmp_path, monkeypatch):
    """The honest boundary: PathGuard IS the confinement mechanism.

    With the guard monkeypatched permissive, an absolute-path write DOES land
    outside the sandbox — there is no second filesystem barrier. The test
    pins the rest of the contract around that escape: the run still completes,
    the audit still records the write as an allow, the quality gate still sees
    ONLY the sandbox (the escaped file is not in ``files_written``), and the
    sandbox itself is still removed. This is the spec's named write-escape
    risk: the mitigation is the guard, and this test proves it is load-bearing.
    """

    class _PermissiveGuard:
        def __init__(self, root):  # noqa: ANN001
            self.root = Path(root)

        def validate(self, value):  # noqa: ANN001
            candidate = Path(value)
            return candidate if candidate.is_absolute() else self.root / candidate

    monkeypatch.setattr(fops_module, "PathGuard", _PermissiveGuard)
    scenario_path = _write_scenario(
        tmp_path,
        _sandbox_scenario_data(max_ruff_violations=None, require_compile_clean=None),
        filename="guard-off.json",
    )

    results, _ = _run(
        scenario_path,
        root=tmp_path,
        turns=[
            _tool_turn("write_file", {"path": str(tmp_path / "escaped.txt"), "content": "escaped"}),
            _text_turn(f"{_MARKER} done"),
        ],
    )

    result = results[0]
    # The escape DID land (with the guard disabled nothing stops it)…
    assert (tmp_path / "escaped.txt").exists()
    assert "write_file" in result.tool_sequence
    # …the gate still measured only the sandbox…
    assert result.quality is not None
    assert result.quality["files_written"] == []
    # …and the run still finished with its sandbox removed.
    assert result.passed, result.failure_reasons
    assert _leaked_sandboxes() == []


# ---------------------------------------------------------------------------
# Quality gate (pure function)
# ---------------------------------------------------------------------------


def test_quality_gate_empty_sandbox_is_a_clean_gate(tmp_path):
    quality = run_quality_gate(tmp_path)

    assert quality == {
        "files_written": [],
        "ruff_violations": 0,
        "compile_ok": True,
        "ruff_note": None,
    }


def test_quality_gate_counts_ruff_violations_and_lists_files(tmp_path):
    (tmp_path / "dirty.py").write_text(_DIRTY_IMPORT, encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "clean.py").write_text(_CLEAN_GREETING, encoding="utf-8")

    quality = run_quality_gate(tmp_path)

    assert quality["files_written"] == ["dirty.py", "nested/clean.py"]
    assert quality["ruff_violations"] >= 1, "the unused import is a default-selected F401"
    assert quality["compile_ok"] is True
    assert quality["ruff_note"] is None


def test_quality_gate_compile_error_sets_compile_ok_false(tmp_path):
    (tmp_path / "broken.py").write_text(_BROKEN_SYNTAX, encoding="utf-8")

    quality = run_quality_gate(tmp_path)

    assert quality["files_written"] == ["broken.py"]
    assert quality["compile_ok"] is False
    # Ruff itself still ran (it tolerates the syntax error); the metric is a number.
    assert quality["ruff_violations"] is not None


def test_quality_gate_missing_ruff_degrades_to_null_with_guidance(tmp_path, monkeypatch):
    (tmp_path / "x.py").write_text(_CLEAN_GREETING, encoding="utf-8")
    monkeypatch.setattr(real_module, "find_spec", lambda name: None)

    quality = run_quality_gate(tmp_path)

    assert quality["ruff_violations"] is None
    assert quality["compile_ok"] is True
    assert quality["ruff_note"] is not None
    assert "uv sync --extra dev" in quality["ruff_note"]


def test_quality_gate_unspawnable_interpreter_degrades_to_null(tmp_path, monkeypatch):
    """The subprocess-level degradation branch: the interpreter cannot spawn."""
    (tmp_path / "x.py").write_text(_CLEAN_GREETING, encoding="utf-8")
    monkeypatch.setattr(real_module, "_python_executable", lambda: "no-such-python-anywhere")

    quality = run_quality_gate(tmp_path)

    assert quality["ruff_violations"] is None
    assert "could not run" in (quality["ruff_note"] or "")


# ---------------------------------------------------------------------------
# Quality assertions — both sides, fail-closed
# ---------------------------------------------------------------------------


def test_violations_exceeding_cap_fails_the_run(tmp_path):
    scenario_path = _write_scenario(
        tmp_path, _sandbox_scenario_data(), filename="cap-exceeded.json"
    )

    results, _ = _run(
        scenario_path,
        root=tmp_path,
        turns=[
            _tool_turn("write_file", {"path": "dirty.py", "content": _DIRTY_IMPORT}),
            _text_turn(f"{_MARKER} written"),
        ],
    )

    result = results[0]
    assert not result.passed
    assert any(
        "exceed max_ruff_violations" in reason
        for reason in result.failure_reasons
        if reason.endswith("max_ruff_violations 0")
    )
    assert result.quality is not None and result.quality["ruff_violations"] >= 1


def test_null_ruff_metric_fails_closed_when_cap_declared(tmp_path, monkeypatch):
    """ruff missing + a declared cap = FAIL with install guidance, never a pass."""
    monkeypatch.setattr(real_module, "find_spec", lambda name: None)
    scenario_path = _write_scenario(tmp_path, _sandbox_scenario_data(), filename="ruff-null.json")

    results, _ = _run(
        scenario_path,
        root=tmp_path,
        turns=[
            _tool_turn("write_file", {"path": "greeting.py", "content": _CLEAN_GREETING}),
            _text_turn(f"{_MARKER} written"),
        ],
    )

    result = results[0]
    assert not result.passed
    assert result.quality is not None and result.quality["ruff_violations"] is None
    assert any(
        "fails closed" in reason and "uv sync --extra dev" in reason
        for reason in result.failure_reasons
    )


def test_compile_dirt_fails_require_compile_clean(tmp_path):
    scenario_path = _write_scenario(
        tmp_path,
        _sandbox_scenario_data(max_ruff_violations=None),
        filename="compile-dirty.json",
    )

    results, _ = _run(
        scenario_path,
        root=tmp_path,
        turns=[
            _tool_turn("write_file", {"path": "broken.py", "content": _BROKEN_SYNTAX}),
            _text_turn(f"{_MARKER} written"),
        ],
    )

    result = results[0]
    assert not result.passed
    assert result.quality is not None and result.quality["compile_ok"] is False
    assert any("require_compile_clean" in reason for reason in result.failure_reasons)


def test_no_quality_assertions_records_the_gate_without_judging(tmp_path):
    """Record-only: the gate block lands in the report, PASS/FAIL is untouched."""
    scenario_path = _write_scenario(
        tmp_path,
        _sandbox_scenario_data(max_ruff_violations=None, require_compile_clean=None),
        filename="record-only.json",
    )

    results, _ = _run(
        scenario_path,
        root=tmp_path,
        turns=[
            _tool_turn("write_file", {"path": "dirty.py", "content": _DIRTY_IMPORT}),
            _text_turn(f"{_MARKER} written"),
        ],
    )

    result = results[0]
    assert result.passed, result.failure_reasons
    assert result.quality is not None
    assert result.quality["ruff_violations"] >= 1
    assert result.quality["compile_ok"] is True
    assert not any("ruff" in reason for reason in result.failure_reasons)


def test_cap_zero_passes_a_clean_write(tmp_path):
    """max_ruff_violations: 0 is a real cap — a clean write passes it."""
    scenario_path = _write_scenario(
        tmp_path, _sandbox_scenario_data(), filename="cap-zero-clean.json"
    )

    results, _ = _run(
        scenario_path,
        root=tmp_path,
        turns=[
            _tool_turn("write_file", {"path": "greeting.py", "content": _CLEAN_GREETING}),
            _text_turn(f"{_MARKER} written"),
        ],
    )

    result = results[0]
    assert result.passed, result.failure_reasons
    assert result.quality is not None and result.quality["ruff_violations"] == 0


def test_quality_assertion_without_gate_block_fails_loudly():
    """Defense in depth: declared assertions + no gate block = failure reason,
    not a silent pass (unreachable via the loader, which requires sandbox_write)."""
    from nexuscli.eval.loader import EvalAssertions

    assertions = EvalAssertions(max_ruff_violations=0)
    assert _quality_assertion_failures(None, assertions)
    assertions = EvalAssertions(require_compile_clean=True)
    assert _quality_assertion_failures(None, assertions)
    # No declared quality keys -> never judged, even with no block.
    assert _quality_assertion_failures(None, EvalAssertions()) == []


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def test_cleanup_success_leaves_no_residue(tmp_path):
    scenario_path = _write_scenario(
        tmp_path,
        _sandbox_scenario_data(max_ruff_violations=None, require_compile_clean=None),
        filename="cleanup-ok.json",
    )

    _run(
        scenario_path,
        root=tmp_path,
        turns=[
            _tool_turn("write_file", {"path": "x.py", "content": _CLEAN_GREETING}),
            _text_turn(_MARKER),
        ],
    )

    assert _leaked_sandboxes() == []


class _BoomShutil:
    """A stand-in for real.py's ``shutil`` whose rmtree always fails."""

    def rmtree(self, path, *args, **kwargs):  # noqa: ANN001, ARG002
        raise OSError("simulated stuck sandbox")


def test_cleanup_failure_warns_once_and_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(real_module, "_SANDBOX_CLEANUP_WARNED", False)
    monkeypatch.setattr(real_module, "shutil", _BoomShutil())
    scenario_path = _write_scenario(
        tmp_path,
        _sandbox_scenario_data(max_ruff_violations=None, require_compile_clean=None),
        filename="cleanup-boom.json",
    )
    turns = [
        _tool_turn("write_file", {"path": "x.py", "content": _CLEAN_GREETING}),
        _text_turn(_MARKER),
    ]

    with warnings.catch_warnings(record=True) as first_run:
        warnings.simplefilter("always")
        results, _ = _run(scenario_path, root=tmp_path, turns=turns)
    cleanup_warnings = [
        warning for warning in first_run if issubclass(warning.category, SandboxCleanupWarning)
    ]
    assert len(cleanup_warnings) == 1, "exactly one warning for the first failed cleanup"
    assert "nexuscli-eval-write-" in str(cleanup_warnings[0].message)
    assert results[0].passed, "the cleanup failure must not fail the run"

    with warnings.catch_warnings(record=True) as second_run:
        warnings.simplefilter("always")
        results2, _ = _run(scenario_path, root=tmp_path, turns=turns)
    assert not [
        warning for warning in second_run if issubclass(warning.category, SandboxCleanupWarning)
    ], "the warning is once per process"
    assert results2[0].passed

    # Remove what the simulated failure left behind (real shutil is untouched).
    for leftover in _leaked_sandboxes():
        shutil.rmtree(Path(tempfile.gettempdir()) / leftover, ignore_errors=True)
    assert _leaked_sandboxes() == []


# ---------------------------------------------------------------------------
# Report — the quality block is additive
# ---------------------------------------------------------------------------


_LEGACY_RUN_KEYS = {
    "index",
    "status",
    "failure_reasons",
    "tool_sequence",
    "turns",
    "usage",
    "cost",
    "judge",
}


def test_report_quality_block_is_additive_legacy_keys_all_equal():
    legacy = real_module.RealEvalResult(name="legacy", passed=True, turns=2, source="a.json")
    sandbox = real_module.RealEvalResult(
        name="sandboxed",
        passed=False,
        turns=3,
        source="b.json",
        failure_reasons=["ruff violations 2 exceed max_ruff_violations 0"],
        quality={
            "files_written": ["greeting.py"],
            "ruff_violations": 2,
            "compile_ok": True,
            "ruff_note": None,
        },
    )

    report = build_real_report([legacy, sandbox], repeat=1)

    entries = {entry["name"]: entry for entry in report["scenarios"]}
    legacy_run = entries["legacy"]["runs"][0]
    # Every legacy key is present with its exact value, and `quality` is the
    # ONLY key added (null for a read-only run).
    assert set(legacy_run.keys()) == _LEGACY_RUN_KEYS | {"quality"}
    assert legacy_run["quality"] is None
    assert legacy_run["index"] == 1
    assert legacy_run["status"] == "pass"
    assert legacy_run["turns"] == 2

    sandbox_run = entries["sandboxed"]["runs"][0]
    assert set(sandbox_run.keys()) == _LEGACY_RUN_KEYS | {"quality"}
    assert sandbox_run["quality"] == {
        "files_written": ["greeting.py"],
        "ruff_violations": 2,
        "compile_ok": True,
        "ruff_note": None,
    }
    assert sandbox_run["status"] == "fail"
    # Legacy aggregates are untouched by the extra field.
    assert entries["legacy"]["pass_k"]["all_pass"] is True


def test_report_serializes_quality_block(tmp_path):
    """The block round-trips through the report document as plain JSON."""
    sandbox = real_module.RealEvalResult(
        name="sandboxed",
        passed=True,
        quality={
            "files_written": ["x.py"],
            "ruff_violations": 0,
            "compile_ok": True,
            "ruff_note": None,
        },
    )

    report = build_real_report([sandbox], repeat=1)
    payload = json.loads(json.dumps(report))

    assert payload["scenarios"][0]["runs"][0]["quality"]["ruff_violations"] == 0


# ---------------------------------------------------------------------------
# Shipped example scenario 04
# ---------------------------------------------------------------------------


def test_builtin_real_set_ships_the_sandbox_scenario():
    real_dir = builtin_real_eval_dir()
    assert real_dir is not None
    scenarios = load_real_eval_scenario(real_dir / "04_sandbox_write_quality.json")

    assert scenarios.name == "real-sandbox-write-quality"
    assert scenarios.sandbox_write is True
    assert scenarios.assertions.final_contains == ["SANDBOX-WRITE-OK"]
    assert scenarios.assertions.max_ruff_violations == 0
    assert scenarios.assertions.require_compile_clean is True
    # The execution channel stays forbidden: the standard trio with bash first.
    assert scenarios.assertions.forbidden_tools is not None
    assert "bash" in scenarios.assertions.forbidden_tools
    assert "execute_command" in scenarios.assertions.forbidden_tools
    assert "task" in scenarios.assertions.forbidden_tools
    # The write pair must NOT be forbidden here — the scenario exists to write.
    assert "write_file" not in scenarios.assertions.forbidden_tools
    assert "edit_file" not in scenarios.assertions.forbidden_tools


def test_all_builtin_tools_are_known_to_the_deny_computation():
    """The deny set covers every builtin; the sandbox variant only lifts the pair."""
    builtin_names = {tool.name for tool in get_builtin_tools()}
    config = _load_real_config(str(tempfile.mkdtemp()))
    deny = set(config.permissions.deny)
    assert builtin_names - REAL_TRACK_TOOLS <= deny
    sandbox_config = copy.deepcopy(config)
    _apply_sandbox_write_config(sandbox_config)
    lifted = deny - set(sandbox_config.permissions.deny)
    assert lifted == set(REAL_SANDBOX_WRITE_TOOLS), (
        "the sandbox variant must lift exactly the write pair and nothing else"
    )
