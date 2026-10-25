"""A7 事件续号（event_seq）测试（spec 2026-10-25_port-approval-semantics）。

Ported from kitagent (MIT, zed123214/kitagent)
src/agent_runtime/core/events/bus.py:66-104：bus 为同 run 事件单调分配
event_seq（自 1 起、严格 +1），resume_event_seq 续号。NexusCLI 形态：
_append_event 落库前从 events 表持久行数推导线程内游标（等价 resume 语义），
把 seq 写进 payload；SSE ``data:`` 即落库 payload 原文，seq 随之透出。
pytest 环境由 conftest 的 autouse fixture 重定向 HOME/USERPROFILE，
runtime.db 落在临时目录。
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import pytest

from nexuscli.config import load_config
from nexuscli.runtime.api import RuntimeApiServer
from tests.test_runtime import _runtime_test_key
from tests.test_runtime_sse import _request, _stream_lines

KEY = _runtime_test_key()


def _make_server(cwd: str) -> RuntimeApiServer:
    return RuntimeApiServer(
        cwd=cwd,
        config=load_config(project_root=None),
        api_key=KEY,
        port=0,
    )


def _rows(server: RuntimeApiServer, thread_id: str) -> list[tuple[int, str, dict[str, Any]]]:
    return [
        (row_id, event_type, json.loads(payload))
        for row_id, event_type, payload in server._events_since(thread_id, 0)
    ]


def _seqs(server: RuntimeApiServer, thread_id: str) -> list[int]:
    return [payload["seq"] for _row_id, _event_type, payload in _rows(server, thread_id)]


def test_same_thread_seq_strictly_increments_across_turns(tmp_path) -> None:
    """同 thread 事件 seq 自 1 起严格 +1；turn.started 边界不重置（线程生命周期单调）。"""

    server = _make_server(str(tmp_path))
    thread_id = server._create_thread()
    first_turn = [
        ("turn.started", {"message": "hi", "mode": "react"}),
        ("message.delta", {"text": "hel"}),
        ("message.delta", {"text": "lo"}),
        ("tool_call", {"name": "bash", "input": {"command": "ls"}}),
        ("tool_result", {"name": "bash", "result": "ok", "is_error": False}),
        ("done", {"total_turns": 1, "total_tokens": 10}),
        ("turn.completed", {"text": "hello", "mode": "react", "total_tokens": 10}),
    ]
    for event_type, payload in first_turn:
        server._append_event(thread_id, event_type, payload)
    # 第二 turn：turn.started 不重置计数，seq 越过上一 turn 继续编号
    server._append_event(thread_id, "turn.started", {"message": "again", "mode": "react"})

    rows = _rows(server, thread_id)
    assert [event_type for _row_id, event_type, _payload in rows] == [
        "thread.created",
        *[event_type for event_type, _payload in first_turn],
        "turn.started",
    ]
    assert _seqs(server, thread_id) == list(range(1, len(rows) + 1))
    # payload 其余键原样保留，只新增 seq
    assert rows[1][2] == {"message": "hi", "mode": "react", "seq": 2}


def test_threads_count_independently(tmp_path) -> None:
    """不同 thread 独立计数（各自自 1 起），events 自增 id 仍为全库共享游标。"""

    server = _make_server(str(tmp_path))
    thread_a = server._create_thread()
    thread_b = server._create_thread()
    server._append_event(thread_a, "message.delta", {"text": "a1"})
    server._append_event(thread_b, "message.delta", {"text": "b1"})
    server._append_event(thread_a, "message.delta", {"text": "a2"})
    server._append_event(thread_b, "message.delta", {"text": "b2"})

    # 独立计数：两线程各自的 seq 都自 1 起严格 +1（thread.created + 2 条）
    assert _seqs(server, thread_a) == [1, 2, 3]
    assert _seqs(server, thread_b) == [1, 2, 3]
    # 对照：events.id（SSE since 游标）是全库共享自增，跨线程不重复
    ids_a = [row_id for row_id, _event_type, _payload in _rows(server, thread_a)]
    ids_b = [row_id for row_id, _event_type, _payload in _rows(server, thread_b)]
    assert not set(ids_a) & set(ids_b)


def test_seq_resumes_after_restart(tmp_path) -> None:
    """换实例（≈重启）后同线程续号越过已持久化游标（kitagent resume_event_seq 语义）。"""

    server = _make_server(str(tmp_path))
    thread_id = server._create_thread()
    server._append_event(thread_id, "message.delta", {"text": "pre"})

    restarted = _make_server(str(tmp_path))  # 同一 runtime.db 的新实例
    restarted._append_event(thread_id, "message.delta", {"text": "post"})
    restarted._append_event(thread_id, "message.delta", {"text": "post2"})

    # thread.created(1) + pre(2) 来自旧实例；post(3)/post2(4) 越过旧游标继续编号
    assert _seqs(restarted, thread_id) == [1, 2, 3, 4]


@pytest.fixture()
def sse_server(tmp_path):
    server = _make_server(str(tmp_path))
    thread = threading.Thread(
        target=server.serve_forever, name="nexuscli-event-seq-test", daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 10
    while server._server is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server._server is not None
    port = int(server._server.server_address[1])
    time.sleep(0.1)
    yield server, port
    server._stop.set()
    thread.join(timeout=5)


def test_sse_data_contains_seq(sse_server) -> None:
    """SSE 帧 data（JSON payload）内含 seq 字段；帧其余部分（id:/event:）不变。"""

    server, port = sse_server
    status, _, body = _request(port, "POST", "/v1/threads", headers={"x-api-key": KEY})
    assert status == 200
    thread_id = str(json.loads(body)["id"])
    server._append_event(thread_id, "message.delta", {"text": "s1"})
    server._append_event(thread_id, "message.delta", {"text": "s2"})

    lines = _stream_lines(port, f"/v1/threads/{thread_id}/events/stream?since=0", seconds=3)
    data_frames = [json.loads(line[6:]) for line in lines if line.startswith("data: ")]
    assert [frame["seq"] for frame in data_frames] == [1, 2, 3]  # thread.created + 2 条
    # 向后兼容：data 仍是单行 JSON，既有键原样保留，前端不读 seq 无影响
    assert data_frames[0] == {"id": thread_id, "seq": 1}
    assert data_frames[1] == {"text": "s1", "seq": 2}
    # id: 重放游标仍是 events 自增 id，行为不变
    id_lines = [line for line in lines if line.startswith("id: ")]
    assert len(id_lines) == 3 and all(line[4:].isdigit() for line in id_lines)
