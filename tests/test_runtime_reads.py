"""F4/F5 服务端读端点测试（spec 2026-10-25_add-workbench-mvp）。

/v1/sessions、/v1/sessions/{id}/transcript、/v1/usage/summary。
conftest 的 HOME 重定向让 ~/.nexuscli/sessions 与 usage.db 落在临时目录。
"""

from __future__ import annotations

import http.client
import json
import threading
import time
import uuid

import pytest

from nexuscli.runtime.api import RuntimeApiServer
from tests.test_runtime import _runtime_test_key

KEY = _runtime_test_key()


@pytest.fixture()
def reads_server(tmp_path):
    from nexuscli.config import load_config

    server = RuntimeApiServer(
        cwd=str(tmp_path), config=load_config(project_root=None), api_key=KEY, port=0
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while server._server is None and time.monotonic() < deadline:
        time.sleep(0.02)
    port = int(server._server.server_address[1])  # type: ignore[index]
    time.sleep(0.1)
    yield server, port, tmp_path
    server._stop.set()
    thread.join(timeout=5)


def _get(port: int, path: str) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path, headers={"x-api-key": KEY})
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, data


def _seed_session(cwd: str, session_id: str, *, project: str | None = None) -> None:
    from nexuscli.runtime.reads import sessions_dir

    sessions_dir().mkdir(parents=True, exist_ok=True)
    meta = {
        "type": "meta",
        "id": session_id,
        "cwd": project if project is not None else cwd,
        "created_at": 1792303810.0,
        "updated_at": 1792303865.0,
        "model": "deepseek-v4-flash",
        "provider": "deepseek",
        "title": "seed session",
        "message_count": 2,
        "forked_from": "",
    }
    user = {"role": "user", "content": "hello"}
    assistant = {"role": "assistant", "content": "hi"}
    usage = {"type": "usage", "model": "deepseek-v4-flash", "total_tokens": 10}
    rows = (meta, user, assistant, usage)
    lines = "\n".join(json.dumps(item, ensure_ascii=False) for item in rows)
    (sessions_dir() / f"{session_id}.jsonl").write_text(lines + "\n", encoding="utf-8")


def test_sessions_list_filters_by_cwd(reads_server) -> None:
    _server, port, tmp = reads_server
    _seed_session(str(tmp), "20261025-000000-aaaaaa")
    _seed_session("/elsewhere/project", "20261025-000001-bbbbbb")

    status, body = _get(port, "/v1/sessions")
    assert status == 200
    payload = json.loads(body)
    ids = [item["id"] for item in payload["sessions"]]
    assert ids == ["20261025-000000-aaaaaa"]

    status, body = _get(port, "/v1/sessions?all=1")
    assert status == 200
    assert len(json.loads(body)["sessions"]) == 2

    # limit 生效
    status, body = _get(port, "/v1/sessions?all=1&limit=1")
    assert len(json.loads(body)["sessions"]) == 1


def test_transcript_reads_and_validation(reads_server) -> None:
    _server, port, tmp = reads_server
    _seed_session(str(tmp), "20261025-000000-cccccc")

    status, body = _get(port, "/v1/sessions/20261025-000000-cccccc/transcript")
    assert status == 200
    payload = json.loads(body)
    assert payload["total"] == 3  # meta 行被剔除，usage 行保留
    assert payload["messages"][0]["role"] == "user"
    assert any(m.get("type") == "usage" for m in payload["messages"])

    status, body = _get(port, "/v1/sessions/20261025-000000-cccccc/transcript?offset=1&limit=1")
    assert json.loads(body)["messages"][0]["role"] == "assistant"

    # 非法 id（路径穿越形态）→ 404；未知 id → 404
    status, _ = _get(port, "/v1/sessions/..%2F..%2Fpasswd/transcript")
    assert status == 404
    status, _ = _get(port, "/v1/sessions/nonexistent-session/transcript")
    assert status == 404
    status, _ = _get(port, "/v1/sessions/bad_id!@/transcript")
    assert status == 404


def test_usage_summary_shape(reads_server) -> None:
    _server, port, _tmp = reads_server
    status, body = _get(port, "/v1/usage/summary?days=7")
    assert status == 200
    payload = json.loads(body)
    # UsageStore.stats 原样形状（02 的 UsageStats 类型逐字段）
    expected_keys = (
        "days",
        "turns",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "total_cost",
        "by_model",
        "by_source",
    )
    for key in expected_keys:
        assert key in payload, key
    assert payload["days"] == 7
    assert isinstance(payload["by_model"], list)
    assert isinstance(payload["by_source"], list)

    status, body = _get(port, "/v1/usage/summary?days=bad")
    assert status == 200  # 非数值回退默认 7 天
    assert json.loads(body)["days"] == 7


def test_reads_require_auth(reads_server) -> None:
    _server, port, _tmp = reads_server
    for path in ("/v1/sessions", "/v1/usage/summary"):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", path)
        response = conn.getresponse()
        assert response.status == 401, path
        conn.close()


def test_reads_do_not_touch_workspace(reads_server) -> None:
    """读端点只读 ~/.nexuscli 固定路径：工作区里的同名文件不会被暴露。"""

    _server, port, tmp = reads_server
    secret = tmp / "secret.jsonl"
    secret.write_text('{"type":"meta","id":"secret","cwd":"x"}\n', encoding="utf-8")
    status, body = _get(port, "/v1/sessions")
    ids = [item["id"] for item in json.loads(body)["sessions"]]
    assert "secret" not in ids
    assert uuid  # silence unused import if needed
