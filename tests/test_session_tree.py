"""Session tree metadata — optional parent_id / branch / created_at in the
session JSONL meta line, fork recording its parent and branch name, and the
``/tree`` rendering helper (direct session_cmds call pattern, as
test_session_store.py's session-surface tests).
"""

from __future__ import annotations

import io
import json

from rich.console import Console

from nexuscli.entrypoints.commands.session_cmds import _tree_command
from nexuscli.session import SessionStore
from nexuscli.session.store import SessionMeta, tree_parent_id
from nexuscli.types import Message


def _make_store(tmp_path):
    return SessionStore(root=tmp_path / "sessions")


def _console() -> Console:
    return Console(file=io.StringIO(), width=200)


def _seed_session(store, cwd: str, content: str = "fix the login bug"):
    writer = store.new_writer(cwd=cwd, model="m1", provider="deepseek")
    writer.append([Message(role="user", content=content)])
    return writer


# --- fork records parent session and branch name ----------------------------


def test_fork_records_parent_id_branch_and_created_at(tmp_path):
    store = _make_store(tmp_path)
    root = _seed_session(store, cwd=str(tmp_path))

    forked = store.fork(root.meta.id)

    assert forked is not None
    assert forked.meta.parent_id == root.meta.id
    assert forked.meta.branch == "fork-1"
    # The legacy snapshot link stays populated alongside the tree fields.
    assert forked.meta.forked_from == root.meta.id
    assert forked.meta.created_at > 0

    # The fields land in the meta line of the forked transcript and survive a
    # load round trip.
    first_line = (
        (store.root / f"{forked.meta.id}.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    data = json.loads(first_line)
    assert data["parent_id"] == root.meta.id
    assert data["branch"] == "fork-1"
    assert data["created_at"] > 0
    reloaded = store.load(forked.meta.id)
    assert reloaded is not None
    assert reloaded.meta.parent_id == root.meta.id
    assert reloaded.meta.branch == "fork-1"


def test_fork_explicit_branch_and_default_numbering(tmp_path):
    store = _make_store(tmp_path)
    root = _seed_session(store, cwd=str(tmp_path))

    first = store.fork(root.meta.id)
    named = store.fork(root.meta.id, branch="experiment")
    second = store.fork(root.meta.id)

    assert first is not None and named is not None and second is not None
    assert first.meta.branch == "fork-1"
    assert named.meta.branch == "experiment"
    # Auto numbering continues the highest fork-N among the parent's children;
    # the explicitly named branch does not consume a number.
    assert second.meta.branch == "fork-2"

    # A sibling fork of a child numbers its own branches from scratch.
    nested = store.fork(first.meta.id)
    assert nested is not None
    assert nested.meta.branch == "fork-1"


def test_fork_unknown_id_returns_none(tmp_path):
    store = _make_store(tmp_path)

    assert store.fork("missing-id") is None


# --- nested forks build the tree --------------------------------------------


def test_nested_fork_links_parent_chain(tmp_path):
    store = _make_store(tmp_path)
    root = _seed_session(store, cwd=str(tmp_path))

    child = store.fork(root.meta.id)
    assert child is not None
    grandchild = store.fork(child.meta.id)
    assert grandchild is not None

    assert child.meta.parent_id == root.meta.id
    assert grandchild.meta.parent_id == child.meta.id
    # The chain is readable back from disk, not just the in-memory metas.
    reloaded_root = store.load(root.meta.id)
    reloaded_child = store.load(child.meta.id)
    reloaded_grandchild = store.load(grandchild.meta.id)
    assert reloaded_root is not None and reloaded_child is not None
    assert reloaded_grandchild is not None
    assert reloaded_root.meta.parent_id == ""
    assert reloaded_child.meta.parent_id == root.meta.id
    assert reloaded_grandchild.meta.parent_id == child.meta.id


# --- legacy transcripts without the new fields ------------------------------


def test_legacy_session_without_new_fields_reads_as_root(tmp_path):
    store = _make_store(tmp_path)
    store.root.mkdir(parents=True)
    legacy_meta = {
        "type": "meta",
        "id": "legacy-root",
        "cwd": str(tmp_path),
        "created_at": 1700000000.0,
        "updated_at": 1700000001.0,
        "model": "m1",
        "provider": "deepseek",
        "title": "legacy session",
        "message_count": 1,
    }
    (store.root / "legacy-root.jsonl").write_text(
        json.dumps(legacy_meta) + "\n" + json.dumps({"role": "user", "content": "old chat"}) + "\n",
        encoding="utf-8",
    )

    record = store.load("legacy-root")

    assert record is not None
    assert [m.content for m in record.messages] == ["old chat"]
    # Missing tree fields degrade to root defaults.
    assert record.meta.parent_id == ""
    assert record.meta.branch == ""
    assert record.meta.created_at == 1700000000.0
    assert tree_parent_id(record.meta) == ""


def test_legacy_forked_from_falls_back_as_tree_parent(tmp_path):
    # A fork recorded before parent_id existed: only the legacy forked_from
    # link is on disk, and the tree hangs it under its source anyway.
    legacy_fork = SessionMeta.from_dict(
        {
            "id": "legacy-fork",
            "cwd": "/x",
            "created_at": 1.0,
            "updated_at": 1.0,
            "forked_from": "legacy-root",
        }
    )

    assert legacy_fork.parent_id == ""
    assert legacy_fork.branch == ""
    assert tree_parent_id(legacy_fork) == "legacy-root"

    # A transcript written before every optional field existed still parses.
    bare = SessionMeta.from_dict({})
    assert bare.parent_id == "" and bare.branch == "" and bare.forked_from == ""


# --- /tree output -------------------------------------------------------------


def test_tree_command_renders_root_with_branches(tmp_path):
    store = _make_store(tmp_path)
    root = _seed_session(store, cwd=str(tmp_path), content="fix the login bug")
    child1 = store.fork(root.meta.id, title="my fork")
    child2 = store.fork(root.meta.id, title="another angle")
    assert child1 is not None and child2 is not None
    grandchild = store.fork(child1.meta.id, title="nested")
    assert grandchild is not None
    console = _console()

    _tree_command("", console, store, root.meta.id)

    out = console.file.getvalue()
    assert "NexusCLI Session Tree" in out
    # Every connected session shows, in parent-before-child order.
    assert out.index(root.meta.id) < out.index(child1.meta.id)
    assert out.index(child1.meta.id) < out.index(grandchild.meta.id)
    assert child2.meta.id in out
    # Branch labels and the current marker on the anchor line.
    assert "(branch: fork-1)" in out
    assert "(branch: fork-2)" in out
    root_line = next(line for line in out.splitlines() if root.meta.id in line)
    assert "(current)" in root_line
    # Tree connectors: the root has two children, the first child one child.
    assert "├─ " in out and "└─ " in out
    assert "│  " in out
    assert "Resume with /resume <id>" in out


def test_tree_command_ancestors_of_nested_fork(tmp_path):
    store = _make_store(tmp_path)
    root = _seed_session(store, cwd=str(tmp_path), content="fix the login bug")
    child1 = store.fork(root.meta.id, title="my fork")
    child2 = store.fork(root.meta.id, title="another angle")
    assert child1 is not None and child2 is not None
    grandchild = store.fork(child1.meta.id, title="nested")
    assert grandchild is not None
    console = _console()

    # Anchored at the grandchild: the parent chain renders up to the root and
    # the unrelated sibling branch (child2) stays out of the tree.
    _tree_command("", console, store, grandchild.meta.id)

    out = console.file.getvalue()
    assert out.index(root.meta.id) < out.index(child1.meta.id)
    assert out.index(child1.meta.id) < out.index(grandchild.meta.id)
    assert child2.meta.id not in out
    # Current marker sits on the anchor, not on its ancestors.
    root_line = next(line for line in out.splitlines() if root.meta.id in line)
    grandchild_line = next(line for line in out.splitlines() if grandchild.meta.id in line)
    assert "(current)" in grandchild_line
    assert "(current)" not in root_line


def test_tree_command_arg_anchors_other_session(tmp_path):
    store = _make_store(tmp_path)
    root = _seed_session(store, cwd=str(tmp_path))
    child = store.fork(root.meta.id, title="my fork")
    assert child is not None
    console = _console()

    # The id argument moves the anchor off the current session.
    _tree_command(child.meta.id, console, store, root.meta.id)

    out = console.file.getvalue()
    assert root.meta.id in out
    child_line = next(line for line in out.splitlines() if child.meta.id in line)
    root_line = next(line for line in out.splitlines() if root.meta.id in line)
    assert "(current)" in child_line
    assert "(current)" not in root_line


def test_tree_command_unpersisted_session_shows_hint(tmp_path):
    store = _make_store(tmp_path)
    console = _console()

    _tree_command("", console, store, "never-persisted-id")

    out = console.file.getvalue()
    assert "NexusCLI Session Tree" in out
    assert "never-persisted-id" in out
    assert "(current)" in out
    assert "no transcript yet" in out


def test_tree_command_unknown_arg_reports_not_found(tmp_path):
    store = _make_store(tmp_path)
    console = _console()

    _tree_command("does-not-exist", console, store, "whatever")

    assert "Session not found" in console.file.getvalue()
