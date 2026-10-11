"""Tests for the parallel workflow scheduler (workflow_cmds._run_workflow_graph).

Covers the behaviours the parallel slice promises:

- dependency-batch correctness: ready steps dispatch together, dependents
  receive upstream reports verbatim;
- the ``workflow.max_parallel_steps`` concurrency upper bound;
- failure semantics: a failing step lets its in-flight batch settle, then
  aborts the remaining steps;
- ``max_parallel_steps=1`` is byte-equivalent to the old serial dispatch;
- the /expert fixed three-step chain still runs through the same scheduler.

Config clamping is pinned here too (the shared tests/test_config.py and
tests/test_workflow.py must not grow), plus the integration contracts: the
scheduler itself records no usage rows (delegated usage is ``source=subagent``
via run_subagent's done-event recorder, covered in test_usage_coverage.py),
and concurrent same-type siblings get distinct, pre-cleaned mailboxes.
"""

from __future__ import annotations

import asyncio
import io
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from rich.console import Console

from nexuscli.agent.subagent import (
    BUILTIN_AGENTS,
    AgentDefinition,
    build_subagent_registry,
    run_subagent,
)
from nexuscli.config import NexusCliConfig, load_config
from nexuscli.entrypoints.commands.workflow_cmds import (
    _run_workflow_graph,
    _step_mailbox_name,
)
from nexuscli.entrypoints.repl import PermissionModeController, _handle_slash
from nexuscli.entrypoints.slash_commands import build_expert_graph
from nexuscli.llm.usage_store import usage_db_path
from nexuscli.tools.base import ToolContext
from nexuscli.workflow import WorkflowGraph, load_workflow

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _console() -> Console:
    return Console(file=io.StringIO(), width=200)


def _console_text(console: Console) -> str:
    return console.file.getvalue()


_STEP_ID_RE = re.compile(r"本次执行的步骤：([A-Za-z0-9_]+)（")


def _step_of(prompt: str) -> str:
    """Extract the dispatched step id from a controlled dispatch prompt."""
    match = _STEP_ID_RE.search(prompt)
    assert match is not None, f"not a dispatch prompt: {prompt!r}"
    return match.group(1)


class _Probe:
    """Fake run_subagent that records dispatch order and peak concurrency."""

    def __init__(self, reports: dict[str, str], failures: set[str] | None = None):
        self.reports = reports
        self.failures = failures or set()
        self.prompts: list[str] = []
        self.starts: list[str] = []  # step ids in start order
        self.ends: list[str] = []  # step ids in completion order
        # Interleaved ("start"|"end", step id) trace, for overlap assertions.
        self.events: list[tuple[str, str]] = []
        self.active = 0
        self.peak = 0

    async def __call__(
        self,
        agent_def: AgentDefinition,
        prompt: str,
        context: ToolContext,
        mailbox_name: str | None = None,
    ) -> str:
        del agent_def, context, mailbox_name
        self.prompts.append(prompt)
        step_id = _step_of(prompt)
        self.starts.append(step_id)
        self.events.append(("start", step_id))
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        self.ends.append(step_id)
        self.events.append(("end", step_id))
        if step_id in self.failures:
            raise RuntimeError(f"boom in {step_id}")
        return self.reports[step_id]


def _agent() -> SimpleNamespace:
    return SimpleNamespace(approval_callback=None)


def _graph(payload: dict[str, Any]) -> WorkflowGraph:
    return load_workflow(payload)


def _run_graph(
    graph: WorkflowGraph,
    probe: _Probe,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: NexusCliConfig | None = None,
) -> Console:
    """Run the scheduler with *probe* faking run_subagent; return the console."""
    console = _console()
    monkeypatch.setattr("nexuscli.agent.subagent.run_subagent", probe)
    asyncio.run(
        _run_workflow_graph(graph, console, str(tmp_path), config or NexusCliConfig(), _agent())
    )
    return console


# ---------------------------------------------------------------------------
# parallel batch correctness
# ---------------------------------------------------------------------------


def test_parallel_batches_dispatch_and_inject_upstream_reports(tmp_path, monkeypatch):
    # Diamond: a, b independent; c joins their reports.
    graph = _graph(
        {
            "steps": [
                {"id": "a", "prompt": "步骤A"},
                {"id": "b", "prompt": "步骤B"},
                {"id": "c", "prompt": "步骤C", "depends_on": ["a", "b"]},
            ]
        }
    )
    probe = _Probe({"a": "报告A全文", "b": "报告B全文", "c": "报告C"})
    console = _run_graph(graph, probe, tmp_path, monkeypatch)

    # a and b start (and finish) before c is even built — one batch of two.
    assert probe.starts.index("a") < probe.starts.index("c")
    assert probe.starts.index("b") < probe.starts.index("c")
    assert set(probe.starts) == {"a", "b", "c"}
    assert probe.peak == 2
    # Upstream reports injected verbatim into the dependent prompt.
    prompt_c = probe.prompts[-1]
    assert _step_of(prompt_c) == "c"
    assert "### a" in prompt_c and "报告A全文" in prompt_c
    assert "### b" in prompt_c and "报告B全文" in prompt_c
    text = _console_text(console)
    assert "Workflow complete — 3 step reports:" in text
    assert "=== a ===" in text and "=== b ===" in text and "=== c ===" in text
    assert "报告C" in text


def test_wave_order_follows_ready_batches_not_blanket_parallel(tmp_path, monkeypatch):
    # Wave 1: a; wave 2: b, c (both depend on a); wave 3: d (depends on b, c).
    graph = _graph(
        {
            "steps": [
                {"id": "a", "prompt": "步骤A"},
                {"id": "b", "prompt": "步骤B", "depends_on": ["a"]},
                {"id": "c", "prompt": "步骤C", "depends_on": ["a"]},
                {"id": "d", "prompt": "步骤D", "depends_on": ["b", "c"]},
            ]
        }
    )
    probe = _Probe({sid: f"报告{sid.upper()}" for sid in "abcd"})
    _run_graph(graph, probe, tmp_path, monkeypatch)

    assert probe.starts == ["a", "b", "c", "d"]
    assert probe.peak == 2  # b and c run together; d waits for both
    for prompt in probe.prompts:
        step_id = _step_of(prompt)
        for dep in graph.steps[step_id].depends_on:
            assert f"### {dep}" in prompt
            assert probe.reports[dep] in prompt


# ---------------------------------------------------------------------------
# concurrency upper bound
# ---------------------------------------------------------------------------


def test_max_parallel_steps_bounds_concurrency(tmp_path, monkeypatch):
    graph = _graph({"steps": [{"id": sid, "prompt": f"步骤{sid}"} for sid in "abcd"]})
    probe = _Probe({sid: f"报告{sid.upper()}" for sid in "abcd"})
    config = NexusCliConfig()
    config.workflow.max_parallel_steps = 2
    console = _run_graph(graph, probe, tmp_path, monkeypatch, config)

    assert probe.peak == 2
    # Batches of two: the first two starts both precede the first completion.
    assert [kind for kind, _ in probe.events[:2]] == ["start", "start"]
    assert probe.starts == ["a", "b", "c", "d"]
    text = _console_text(console)
    assert "Workflow complete — 4 step reports:" in text


def test_max_parallel_steps_larger_than_graph_is_clamped_by_ready_set(tmp_path, monkeypatch):
    graph = _graph({"steps": [{"id": sid, "prompt": f"步骤{sid}"} for sid in "abc"]})
    probe = _Probe({sid: f"报告{sid.upper()}" for sid in "abc"})
    config = NexusCliConfig()
    config.workflow.max_parallel_steps = 8
    _run_graph(graph, probe, tmp_path, monkeypatch, config)

    assert probe.peak == 3
    assert probe.starts == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# failure semantics: let the batch settle, then abort
# ---------------------------------------------------------------------------


def test_failure_in_first_batch_settles_siblings_then_aborts(tmp_path, monkeypatch):
    graph = _graph(
        {
            "steps": [
                {"id": "a", "prompt": "步骤A"},
                {"id": "b", "prompt": "步骤B"},
                {"id": "c", "prompt": "步骤C", "depends_on": ["a", "b"]},
            ]
        }
    )
    probe = _Probe({sid: f"报告{sid.upper()}" for sid in "abc"}, failures={"a"})
    console = _run_graph(graph, probe, tmp_path, monkeypatch)

    # The failed batch ran to completion (no cancellation) — b finished — but
    # the dependent wave c was never dispatched.
    assert probe.starts == ["a", "b"]
    assert "b" in probe.ends
    text = _console_text(console)
    assert "Step a failed: boom in a" in text
    assert "中止剩余步骤：c" in text
    assert "Workflow complete" not in text


def test_failure_in_later_wave_aborts_only_downstream_steps(tmp_path, monkeypatch):
    graph = _graph(
        {
            "steps": [
                {"id": "a", "prompt": "步骤A"},
                {"id": "b", "prompt": "步骤B", "depends_on": ["a"]},
                {"id": "c", "prompt": "步骤C", "depends_on": ["a"]},
                {"id": "d", "prompt": "步骤D", "depends_on": ["b", "c"]},
            ]
        }
    )
    probe = _Probe({sid: f"报告{sid.upper()}" for sid in "abcd"}, failures={"b"})
    console = _run_graph(graph, probe, tmp_path, monkeypatch)

    # Wave 2 (b, c) settled: c succeeded. Wave 3 (d) was aborted.
    assert probe.starts == ["a", "b", "c"]
    text = _console_text(console)
    assert "Step b failed: boom in b" in text
    assert "中止剩余步骤：d" in text
    assert "Workflow complete" not in text


# ---------------------------------------------------------------------------
# max_parallel_steps=1 is serial-equivalent
# ---------------------------------------------------------------------------


def test_max_parallel_one_degenerates_to_serial_dispatch(tmp_path, monkeypatch):
    graph = _graph(
        {
            "steps": [
                {"id": "research", "prompt": "调研"},
                {"id": "implement", "prompt": "实现", "depends_on": ["research"]},
                {"id": "summarize", "prompt": "汇总", "depends_on": ["implement"]},
            ]
        }
    )
    probe = _Probe({"research": "报告一", "implement": "报告二", "summarize": "报告三"})
    config = NexusCliConfig()
    config.workflow.max_parallel_steps = 1
    console = _run_graph(graph, probe, tmp_path, monkeypatch, config)

    assert probe.peak == 1
    assert [_step_of(p) for p in probe.prompts] == ["research", "implement", "summarize"]
    # Serial-equivalent upstream injection.
    assert "### research" in probe.prompts[1] and "报告一" in probe.prompts[1]
    assert "### implement" in probe.prompts[2] and "报告二" in probe.prompts[2]
    text = _console_text(console)
    assert "Workflow complete — 3 step reports:" in text
    assert all(f"=== {sid} ===" in text for sid in ("research", "implement", "summarize"))


def test_max_parallel_one_with_independent_steps_stays_strictly_serial(tmp_path, monkeypatch):
    graph = _graph({"steps": [{"id": sid, "prompt": f"步骤{sid}"} for sid in "ab"]})
    probe = _Probe({"a": "报告A", "b": "报告B"})
    config = NexusCliConfig()
    config.workflow.max_parallel_steps = 1
    _run_graph(graph, probe, tmp_path, monkeypatch, config)

    # b starts strictly after a finishes.
    assert probe.events.index(("start", "b")) > probe.events.index(("end", "a"))


# ---------------------------------------------------------------------------
# /expert chain through the parallel scheduler
# ---------------------------------------------------------------------------


def test_expert_chain_unaffected_by_parallel_scheduler(tmp_path, monkeypatch):
    graph = build_expert_graph("重构登录模块")
    reports = {"research": "调研要点清单", "execute": "核心工作完成", "report": "最终综合报告"}
    probe = _Probe(reports)
    console = _run_graph(graph, probe, tmp_path, monkeypatch)

    # A linear chain can only ever dispatch one step per wave, however large
    # max_parallel_steps is — the expert loop keeps its serial semantics.
    assert [_step_of(p) for p in probe.prompts] == ["research", "execute", "report"]
    assert probe.peak == 1
    assert all("重构登录模块" in p for p in probe.prompts)
    text = _console_text(console)
    assert "Workflow complete — 3 step reports:" in text
    for report in ("调研要点清单", "核心工作完成", "最终综合报告"):
        assert report in text


def _run_slash(message: str, tmp_path: Path) -> Console:
    from nexuscli.session import SessionStore

    store = SessionStore(root=tmp_path / "sessions")
    writer = store.new_writer(cwd=str(tmp_path), model="m1", provider="deepseek")
    console = _console()
    asyncio.run(
        _handle_slash(
            message,
            console,
            str(tmp_path),
            NexusCliConfig(),
            SimpleNamespace(history=[], cwd=str(tmp_path), approval_callback=None),
            None,
            PermissionModeController(NexusCliConfig()),
            None,
            SimpleNamespace(store=store, writer=writer),
        )
    )
    return console


def test_expert_slash_command_end_to_end(tmp_path, monkeypatch):
    probe = _Probe({"research": "调研要点清单", "execute": "核心工作完成", "report": "最终报告"})
    monkeypatch.setattr("nexuscli.agent.subagent.run_subagent", probe)

    console = _run_slash("/expert 重构登录模块", tmp_path)

    assert [_step_of(p) for p in probe.prompts] == ["research", "execute", "report"]
    text = _console_text(console)
    assert "dispatching step research" in text
    assert "Workflow complete — 3 step reports:" in text
    assert "最终报告" in text


# ---------------------------------------------------------------------------
# config section: default, override and clamping
# ---------------------------------------------------------------------------


def test_workflow_config_default_and_clamping():
    assert NexusCliConfig().workflow.max_parallel_steps == 3

    cases = [
        ({"workflow": {"max_parallel_steps": 7}}, 7),
        ({"workflow": {"max_parallel_steps": 0}}, 1),
        ({"workflow": {"max_parallel_steps": -5}}, 1),
        ({"workflow": {"max_parallel_steps": "abc"}}, 3),
        ({"workflow": {"max_parallel_steps": None}}, 3),
    ]
    for overrides, expected in cases:
        assert load_config(overrides=overrides).workflow.max_parallel_steps == expected


# ---------------------------------------------------------------------------
# usage unification: the scheduler records nothing itself
# ---------------------------------------------------------------------------


def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect Path.home() into a temp home (mirrors test_usage_history.py)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def _usage_rows(home: Path) -> list[tuple[str, str, str, int]]:
    import sqlite3

    conn = sqlite3.connect(usage_db_path(home))
    rows = conn.execute(
        "select session_id, model, provider, total_tokens from turn_usage"
    ).fetchall()
    conn.close()
    return [(row[0], row[1], row[2], row[3]) for row in rows]


def test_scheduler_itself_writes_no_usage_rows(tmp_path, monkeypatch):
    """Zero-token per-step rows are gone: delegated usage is recorded once, as
    ``source=subagent`` by run_subagent's done-event recorder (pinned in
    test_usage_coverage.py). With a probe standing in for run_subagent the
    scheduler must not create any usage.db row — not even a zero-token one,
    and not for a session-less config either (no guard left to bypass).
    """
    home = _isolate_home(tmp_path, monkeypatch)
    graph = _graph({"steps": [{"id": "a", "prompt": "步骤A"}, {"id": "b", "prompt": "步骤B"}]})
    probe = _Probe({"a": "报告A", "b": "报告B"})
    config = NexusCliConfig()
    config.policy.session_id = "wf-session"

    _run_graph(graph, probe, tmp_path, monkeypatch, config)

    db_path = usage_db_path(home)
    if db_path.exists():
        assert _usage_rows(home) == []


# ---------------------------------------------------------------------------
# per-step mailbox isolation for concurrent siblings
# ---------------------------------------------------------------------------


def test_step_mailbox_name_sanitizes_and_deduplicates():
    used: set[str] = set()
    names = [
        _step_mailbox_name(step_id, used)
        for step_id in ("a", "a b", "a.b", "调研 阶段", "..", "a", "")
    ]
    # Mailbox-legal names (mailbox._MAILBOX_NAME_RE), all distinct, stable
    # order-independent uniqueness via the shared `used` set.
    import re

    pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    for name in names:
        assert pattern.fullmatch(name), name
    assert len(set(names)) == len(names)
    # Readable, deterministic form for the simple ids.
    assert names[0] == "general-purpose.a"
    assert names[1] == "general-purpose.a_b"
    # A CJK-only id collapses to the placeholder instead of crashing.
    assert names[3].startswith("general-purpose.")


def test_parallel_steps_get_distinct_mailbox_names_and_prompt_advertising(tmp_path, monkeypatch):
    graph = _graph(
        {
            "steps": [
                {"id": "step one", "prompt": "步骤一"},
                {"id": "step/two", "prompt": "步骤二"},
            ]
        }
    )
    probe = _Probe({"step one": "报告一", "step/two": "报告二"})
    _run_graph(graph, probe, tmp_path, monkeypatch)

    # Each prompt advertises its own mailbox name and the batch sibling's —
    # so cross-step mailbox_post can actually address a sibling. The capture
    # stops at the full-width paren that follows the name in the prompt.
    own_names = [
        re.search(r"你的邮箱代理名：([^\s（]+)", prompt).group(1) for prompt in probe.prompts
    ]
    assert own_names[0] != own_names[1]
    for prompt, sibling_name in zip(probe.prompts, reversed(own_names), strict=True):
        assert f"同批次并发步骤的邮箱代理名：{sibling_name}" in prompt


def test_expert_chain_gets_own_mailbox_names_but_no_peers(tmp_path, monkeypatch):
    """The /expert chain routes through the same scheduler, so each of its
    steps also gets a distinct mailbox name — but linear chains are one step
    per batch, so no sibling names are advertised.
    """
    graph = build_expert_graph("重构登录模块")
    probe = _Probe({"research": "一", "execute": "二", "report": "三"})
    console = _run_graph(graph, probe, tmp_path, monkeypatch)

    own_names = []
    for prompt in probe.prompts:
        match = re.search(r"你的邮箱代理名：([^\s（]+)", prompt)
        assert match is not None, prompt
        own_names.append(match.group(1))
        assert "同批次并发步骤的邮箱代理名" not in prompt
    assert len(set(own_names)) == 3
    assert "Workflow complete — 3 step reports:" in _console_text(console)


# ---------------------------------------------------------------------------
# subagent concurrency safety (fresh instances per dispatch)
# ---------------------------------------------------------------------------


def test_concurrent_run_subagent_builds_isolated_registries(monkeypatch, tmp_path):
    """Concurrent dispatch exercises the real run_subagent path twice at once.

    run_subagent builds a fresh Agent, ToolRegistry and LLM client per call
    (subagent.py) — this test proves concurrent calls do not share registry
    instances and both reports come back through the real session machinery
    (stubbed only at the LLM boundary).
    """
    registries: list[Any] = []
    original_builder = build_subagent_registry

    def spy_builder(agent_def: AgentDefinition, mailbox_name: str | None = None) -> Any:
        del mailbox_name
        registry = original_builder(agent_def)
        registries.append(registry)
        return registry

    monkeypatch.setattr("nexuscli.agent.subagent.build_subagent_registry", spy_builder)

    async def fake_run(self, message: str):  # noqa: ANN001 - async generator method
        del self
        await asyncio.sleep(0.01)  # force real overlap between the two calls
        yield {"type": "text_delta", "text": f"报告[{message}]"}

    monkeypatch.setattr("nexuscli.agent.agent.Agent.run", fake_run)

    class _StubClient:
        model_name = "stub-model"
        provider_name = "stub-provider"
        max_context_window = 100_000

    monkeypatch.setattr(
        "nexuscli.llm.factory.create_llm_client",
        lambda config, telemetry_enabled=False: _StubClient(),
    )

    config = NexusCliConfig()
    # Placeholder credential (repo test convention, tests/test_subagent.py:34);
    # _validate_config refuses an empty key before any run.
    config.llm.api_key = "key"
    context = ToolContext(cwd=str(tmp_path), config=config)
    definition = BUILTIN_AGENTS["general-purpose"]

    async def _dispatch_both() -> list[str]:
        # gather must run inside a live loop: outside asyncio.run it eagerly
        # ensure_futures its arguments and fails with "no current event loop".
        return await asyncio.gather(
            run_subagent(definition, "步骤一提示词", context),
            run_subagent(definition, "步骤二提示词", context),
        )

    reports = asyncio.run(_dispatch_both())

    assert reports == ["报告[步骤一提示词]", "报告[步骤二提示词]"]
    # One fresh registry per concurrent dispatch, never shared.
    assert len(registries) == 2
    assert registries[0] is not registries[1]
