from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from nexuscli.agent import Agent, PlanExecuteAgent
from nexuscli.agent.orchestrator import AgentOrchestrator
from nexuscli.config import FeatureConfig, NexusCliConfig
from nexuscli.snapshot import SnapshotService
from nexuscli.tools import ToolRegistry


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect Path.home() into a temp home.

    SnapshotService roots under ``~/.nexuscli/snapshots``; a HOME-only patch
    leaks onto the real home on Windows, where Path.home() reads USERPROFILE.
    Triple patch, mirroring tests/test_plan.py.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


class _NaturalEndClient:
    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        yield {"type": "text_delta", "text": "all done"}
        yield {"type": "message_end", "stop_reason": "end_turn"}


class _FailingPlanner:
    """Planner stub that raises before producing a plan; PlanExecuteAgent.run
    turns this into an error event, and the post-turn snapshot still fires in
    its finally block — exactly the pairing the gate must cover."""

    async def stream_plan(self, goal: str):  # noqa: ARG002
        raise ValueError("planner offline")
        yield  # pragma: no cover — unreachable; marks this an async generator


async def _collect_events(agent) -> list[dict[str, Any]]:
    return [event async for event in agent.run("hello")]


def _agent_config(auto_snapshot: bool) -> NexusCliConfig:
    """Deterministic config: no config-file lookup, fake key (Agent
    validates it eagerly in _validate_config)."""
    config = NexusCliConfig()
    config.llm.api_key = "test-key"
    config.features.auto_snapshot = auto_snapshot
    return config


def _agent(config: NexusCliConfig, project: Path) -> Agent:
    return Agent(
        llm_client=_NaturalEndClient(),
        tool_registry=ToolRegistry(),
        config=config,
        cwd=str(project),
    )


def _snapshot_root(home: Path) -> Path:
    return home / ".nexuscli" / "snapshots"


def test_snapshot_restore(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    file_path = project / "note.txt"
    file_path.write_text("before", encoding="utf-8")

    service = SnapshotService(project)
    first = service.create("pre-turn")
    file_path.write_text("after", encoding="utf-8")

    restored = service.restore(first.id)

    assert restored.id == first.id
    assert file_path.read_text(encoding="utf-8") == "before"


def test_create_incremental_skips_unchanged_files(tmp_path: Path, isolated_home: Path):
    """A second create() against the same project only copies what changed:
    unchanged files are not rewritten (their copies in the new snapshot keep
    the previous snapshot's mtime), a modified file is refreshed, a new file
    is copied in, index.jsonl keeps its row format, and restore still yields
    the complete tree."""
    project = tmp_path / "project"
    project.mkdir()
    keep = project / "keep.txt"
    keep.write_text("keep", encoding="utf-8")
    same = project / "same.txt"
    same.write_text("same", encoding="utf-8")
    nested = project / "nested"
    nested.mkdir()
    deep = nested / "deep.txt"
    deep.write_text("deep", encoding="utf-8")
    # Pin mtimes so the (mtime, size) fingerprint is deterministic even when
    # the two creates land inside the same clock tick.
    for pinned in (keep, same, deep):
        os.utime(pinned, (1_000_000_000, 1_000_000_000))

    service = SnapshotService(project)
    first = service.create("pre-turn")

    keep.write_text("keep-v2", encoding="utf-8")
    (project / "added.txt").write_text("added", encoding="utf-8")
    second = service.create("post-turn")

    # unchanged files (top level and nested): not rewritten — the new
    # snapshot's copies kept the mtime of the previous snapshot's copies
    first_same_mtime = (first.path / "same.txt").stat().st_mtime
    assert first_same_mtime == pytest.approx(1_000_000_000)
    assert (second.path / "same.txt").stat().st_mtime == first_same_mtime
    first_deep_mtime = (first.path / "nested" / "deep.txt").stat().st_mtime
    assert (second.path / "nested" / "deep.txt").stat().st_mtime == first_deep_mtime
    # modified file refreshed, new file copied in, no stray manifest file
    assert (second.path / "keep.txt").read_text(encoding="utf-8") == "keep-v2"
    assert (second.path / "added.txt").read_text(encoding="utf-8") == "added"
    assert {entry.name for entry in second.path.iterdir()} == {
        "keep.txt",
        "same.txt",
        "added.txt",
        "nested",
    }

    # index.jsonl row format untouched (rows are appended oldest-first)
    rows = [
        json.loads(line) for line in service.index_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [row["id"] for row in rows] == [first.id, second.id]
    assert set(rows[0]) == {"id", "phase", "created_at", "path", "skipped"}

    # restore still yields the complete tree
    restored = service.restore(second.id)
    assert restored.id == second.id
    assert keep.read_text(encoding="utf-8") == "keep-v2"
    assert deep.read_text(encoding="utf-8") == "deep"
    assert (project / "added.txt").read_text(encoding="utf-8") == "added"


def test_auto_snapshot_disabled_by_default_and_skips_dirs(tmp_path, isolated_home):
    """features.auto_snapshot defaults to off, and a react agent run then
    creates no snapshot root, index or records — the SnapshotService is never
    even constructed, so the pointless mkdir is skipped too."""
    assert NexusCliConfig().features.auto_snapshot is False
    assert FeatureConfig().auto_snapshot is False

    project = tmp_path / "project"
    project.mkdir()
    agent = _agent(_agent_config(auto_snapshot=False), project)

    events = asyncio.run(_collect_events(agent))

    assert events[-1]["type"] == "done"
    assert not _snapshot_root(isolated_home).exists()


def test_auto_snapshot_enabled_behaves_like_before(tmp_path, isolated_home):
    """With the flag on, a react agent run records pre-turn + post-turn
    snapshots and the matching index rows — identical to the pre-flag
    behaviour."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "note.txt").write_text("content", encoding="utf-8")

    events = asyncio.run(_collect_events(_agent(_agent_config(auto_snapshot=True), project)))

    assert events[-1]["type"] == "done"
    records = SnapshotService(project).list()  # newest-first
    assert [record.phase for record in records] == ["post-turn", "pre-turn"]


def test_tool_cache_dirs_are_skipped(tmp_path, isolated_home):
    """Regenerable tool-state caches are excluded from snapshots: a scanner
    workdir like .mimosa can hold tens of thousands of files and would turn
    every snapshot into a full-tree crawl."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "real.txt").write_text("real", encoding="utf-8")
    for cache in (".mimosa", ".ruff_cache", ".pytest_cache"):
        (project / cache).mkdir()
        (project / cache / "blob.bin").write_text("x" * 128, encoding="utf-8")

    record = SnapshotService(project).create("pre-turn")

    captured = {path.relative_to(record.path).as_posix() for path in record.path.rglob("*")}
    assert "real.txt" in captured
    assert not any(name in captured for name in (".mimosa", ".ruff_cache", ".pytest_cache"))


def test_plan_execute_snapshot_gate(tmp_path, isolated_home):
    """PlanExecuteAgent honours the same flag: off → no snapshot artifacts
    even when the run fails early; on → pre-turn + post-turn rows still land
    in the index (the finally block keeps firing around the error event)."""
    project = tmp_path / "project"
    project.mkdir()

    off = PlanExecuteAgent(
        llm_client=_NaturalEndClient(),
        tool_registry=ToolRegistry(),
        config=NexusCliConfig(),
        cwd=str(project),
        planner=_FailingPlanner(),
    )
    off_events = asyncio.run(_collect_events(off))
    assert any(event["type"] == "error" for event in off_events)
    assert not _snapshot_root(isolated_home).exists()

    config = NexusCliConfig()
    config.features.auto_snapshot = True
    on = PlanExecuteAgent(
        llm_client=_NaturalEndClient(),
        tool_registry=ToolRegistry(),
        config=config,
        cwd=str(project),
        planner=_FailingPlanner(),
    )
    asyncio.run(_collect_events(on))

    records = SnapshotService(project).list()
    assert [record.phase for record in records] == ["post-turn", "pre-turn"]


def test_orchestrator_snapshot_gate(tmp_path, isolated_home):
    """AgentOrchestrator.run honours the same flag: off → no snapshot
    artifacts even though the run fails in the planner phase; on →
    pre-turn + post-turn rows appear (post-turn from the finally block)."""

    class _BadJsonClient:
        """Planner replies '{}' forever: parse fails, repair fails, and
        _plan_steps raises after planner_attempts — deterministic, no tools."""

        model_name = "fake-model"
        provider_name = "fake-provider"
        max_context_window = 1000

        async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
            yield {"type": "text_delta", "text": "{}"}
            yield {"type": "message_end", "stop_reason": "end_turn"}

    project = tmp_path / "project"
    project.mkdir()

    def _orchestrator(config: NexusCliConfig) -> AgentOrchestrator:
        return AgentOrchestrator(
            llm_client=_BadJsonClient(),
            tool_registry=ToolRegistry(),
            config=config,
            cwd=str(project),
        )

    off_events = asyncio.run(_collect_events(_orchestrator(NexusCliConfig())))
    assert any(event["type"] == "error" for event in off_events)
    assert not _snapshot_root(isolated_home).exists()

    config = NexusCliConfig()
    config.features.auto_snapshot = True
    on_events = asyncio.run(_collect_events(_orchestrator(config)))
    assert any(event["type"] == "error" for event in on_events)

    records = SnapshotService(project).list()
    assert [record.phase for record in records] == ["post-turn", "pre-turn"]
