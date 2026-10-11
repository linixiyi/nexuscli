"""Tests for cross-session transcript search (/history, W-G4).

``SessionStore.search`` scans session JSONL transcripts for a
case-insensitive keyword in message content; the ``/history`` slash command
renders the hits and points at ``/resume <id>``.
"""

from __future__ import annotations

import asyncio
import io

from rich.console import Console

from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints.repl import PermissionModeController, ReplSessionState, _handle_slash
from nexuscli.session import SessionStore
from nexuscli.types import Message


def _make_store(tmp_path):
    return SessionStore(root=tmp_path / "sessions")


def _seed_session(store, cwd: str, *contents: str):
    writer = store.new_writer(cwd=cwd, model="m1", provider="deepseek")
    writer.append([Message(role="user", content=c) for c in contents])
    return writer


def _console() -> Console:
    return Console(file=io.StringIO(), width=200)


def _run_slash(raw: str, console: Console, tmp_path, state) -> bool:
    return asyncio.run(
        _handle_slash(
            raw,
            console,
            str(tmp_path),
            NexusCliConfig(),
            _agent(tmp_path),
            None,
            PermissionModeController(NexusCliConfig()),
            None,
            state,
        )
    )


def _agent(tmp_path):
    from types import SimpleNamespace

    return SimpleNamespace(
        history=[],
        cwd=str(tmp_path),
        llm_client=SimpleNamespace(model_name="m1", provider_name="p"),
        last_usage=None,
        last_cost={},
    )


def test_search_matches_message_content_case_insensitive(tmp_path):
    store = _make_store(tmp_path)
    writer = _seed_session(store, str(tmp_path), "find the failing test", "second message")

    hits = store.search("FIND")

    assert len(hits) == 1
    hit = hits[0]
    assert hit.session_id == writer.meta.id
    assert hit.title == "find the failing test"
    assert "failing" in hit.snippet.lower()
    assert hit.cwd == str(tmp_path)
    assert hit.updated_at == writer.meta.updated_at


def test_search_is_one_hit_per_session_newest_first(tmp_path):
    store = _make_store(tmp_path)
    older = _seed_session(store, str(tmp_path), "alpha topic")
    newer = _seed_session(store, str(tmp_path), "alpha again")
    # Force deterministic ordering independent of file timestamps.
    newer.meta.updated_at = older.meta.updated_at + 10
    newer._write_meta()

    hits = store.search("alpha")

    assert [h.session_id for h in hits] == [newer.meta.id, older.meta.id]


def test_search_filters_by_cwd_without_all(tmp_path):
    store = _make_store(tmp_path)
    project_a = _seed_session(store, "/proj/a", "shared keyword here")
    other = _seed_session(store, "/proj/b", "shared keyword there")

    hits_a = [h.session_id for h in store.search("shared", cwd="/proj/a")]
    assert hits_a == [project_a.meta.id]
    assert other.meta.id not in hits_a
    hits_all = {h.session_id for h in store.search("shared", cwd=None)}
    assert hits_all == {project_a.meta.id, other.meta.id}


def test_search_empty_query_returns_nothing(tmp_path):
    store = _make_store(tmp_path)
    _seed_session(store, str(tmp_path), "hello")

    assert store.search("") == []
    assert store.search("   ") == []


def test_search_skips_usage_records_and_corrupt_lines(tmp_path):
    store = _make_store(tmp_path)
    writer = _seed_session(store, str(tmp_path), "hello world")
    path = writer.path
    lines = path.read_text(encoding="utf-8").splitlines()
    lines.append(
        '{"type": "usage", "ts": "2026-10-11 10:00:00", "total_tokens": 7, "input_tokens": 5}'
    )
    lines.append("{corrupt}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # The usage record's token text must not surface as a content match.
    assert store.search("total_tokens") == []
    assert [h.session_id for h in store.search("hello")] == [writer.meta.id]


def test_search_snippet_ellipsizes_long_content(tmp_path):
    store = _make_store(tmp_path)
    long_text = "padding " * 40 + "needle here " + "trailer " * 40
    _seed_session(store, str(tmp_path), long_text)

    hits = store.search("needle")

    assert len(hits) == 1
    snippet = hits[0].snippet
    assert "needle" in snippet
    assert snippet.startswith("…") and snippet.endswith("…")
    assert len(snippet) < 200


def test_history_slash_empty_arg_is_friendly(tmp_path):
    store = _make_store(tmp_path)
    state = ReplSessionState(store=store, writer=store.new_writer(cwd=str(tmp_path)))
    console = _console()

    should_exit = _run_slash("/history", console, tmp_path, state)

    assert should_exit is False
    text = console.file.getvalue()
    assert "Usage:" in text
    assert "--all" in text


def test_history_slash_lists_hits_with_resume_hint(tmp_path):
    store = _make_store(tmp_path)
    writer = _seed_session(store, str(tmp_path), "fix the login bug")
    state = ReplSessionState(store=store, writer=store.new_writer(cwd=str(tmp_path)))
    console = _console()

    should_exit = _run_slash("/history login", console, tmp_path, state)

    assert should_exit is False
    text = console.file.getvalue()
    assert writer.meta.id in text
    assert "fix the login bug" in text
    assert "/resume" in text


def test_history_slash_no_matches_prints_placeholder(tmp_path):
    store = _make_store(tmp_path)
    state = ReplSessionState(store=store, writer=store.new_writer(cwd=str(tmp_path)))
    console = _console()

    should_exit = _run_slash("/history nothing-matches-this", console, tmp_path, state)

    assert should_exit is False
    assert "(no matches)" in console.file.getvalue()


def test_history_slash_all_flag_crosses_projects(tmp_path):
    store = _make_store(tmp_path)
    other_writer = _seed_session(store, "/proj/other", "quartz mining notes")
    state = ReplSessionState(store=store, writer=store.new_writer(cwd=str(tmp_path)))
    console = _console()

    should_exit = _run_slash("/history quartz", console, tmp_path, state)
    assert "(no matches)" in console.file.getvalue()

    console2 = _console()
    should_exit = _run_slash("/history quartz --all", console2, tmp_path, state)

    assert should_exit is False
    text = console2.file.getvalue()
    assert other_writer.meta.id in text
    assert "all projects" in text
