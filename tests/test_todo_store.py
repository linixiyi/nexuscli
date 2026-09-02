from __future__ import annotations

import pytest

from nexuscli.tools.todo_store import TodoStore, format_todos


def test_set_normalizes_and_persists(tmp_path):
    store = TodoStore(str(tmp_path))
    todos = store.set(
        [
            {"content": "write tests", "status": "in_progress", "priority": "high"},
            {"content": "ship it", "status": "pending"},
        ]
    )

    assert todos[0]["priority"] == "high"
    assert todos[1]["status"] == "pending"
    assert todos[1]["priority"] == "medium"
    assert store.get() == todos


def test_get_returns_empty_for_missing_or_corrupt_file(tmp_path):
    store = TodoStore(str(tmp_path))
    assert store.get() == []

    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not json", encoding="utf-8")
    assert store.get() == []

    store.path.write_text('{"oops": true}', encoding="utf-8")
    assert store.get() == []


def test_clear_empties_the_list(tmp_path):
    store = TodoStore(str(tmp_path))
    store.set([{"content": "one", "status": "pending"}])

    store.clear()

    assert store.get() == []
    assert format_todos(store.get()) == "(no todos)"


@pytest.mark.parametrize(
    "payload",
    [
        "not-a-list",
        [[]] * 60,
        [{"content": "  "}],
        [{"content": "x", "status": "bogus"}],
        [{"content": "x", "priority": "urgent"}],
        [42],
    ],
)
def test_set_rejects_invalid_payloads(tmp_path, payload):
    store = TodoStore(str(tmp_path))
    with pytest.raises(ValueError):
        store.set(payload)


def test_format_todos_marks_status_and_non_medium_priority():
    lines = format_todos(
        [
            {"content": "done step", "status": "completed", "priority": "medium"},
            {"content": "current step", "status": "in_progress", "priority": "high"},
            {"content": "later step", "status": "pending", "priority": "low"},
        ]
    ).splitlines()

    assert lines[0].startswith("1. ☑ [completed] done step")
    assert lines[1].startswith("2. ▸ [in_progress] current step (high)")
    assert lines[2].startswith("3. ☐ [pending] later step (low)")
