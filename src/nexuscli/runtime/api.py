from __future__ import annotations

import asyncio
import hmac
import json
import mimetypes
import os
import posixpath
import sqlite3
import threading
import time
from contextlib import suppress
from dataclasses import asdict
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from nexuscli.agent import QueryEngine
from nexuscli.bootstrap import build_tool_registry
from nexuscli.config import NexusCliConfig
from nexuscli.llm import create_llm_client
from nexuscli.net_guards import is_loopback_origin
from nexuscli.runtime import reads
from nexuscli.runtime.tasks import TASK_MODES, DurableTaskManager, TaskRecord
from nexuscli.types import Message

MAX_SSE_CLIENTS = 16
SSE_HEARTBEAT_SECONDS = 15


class TaskCanceledError(RuntimeError):
    pass


class RuntimeApiServer:
    def __init__(
        self,
        *,
        cwd: str,
        config: NexusCliConfig,
        api_key: str | None = None,
        port: int = 8080,
        workers: int = 2,
        workbench: bool = False,
    ):
        self.cwd = str(Path(cwd).resolve())
        self.config = config
        self.api_key = api_key
        self.port = port
        self.workbench = workbench
        # SSE 客户端槽位与事件唤醒信号（DB 仍是唯一事实源，Condition 只消除轮询延迟）
        self._sse_slots = threading.BoundedSemaphore(MAX_SSE_CLIENTS)
        self._event_cond = threading.Condition()
        self._server: ThreadingHTTPServer | None = None
        self.db_path = Path.home() / ".nexuscli" / "runtime" / "runtime.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.task_manager = DurableTaskManager(
            Path.home() / ".nexuscli" / "tasks" / "tasks.db", scope=self.cwd
        )
        self.workers = workers
        self._stop = threading.Event()
        # One turn at a time per thread: concurrent turns interleaved their
        # event streams and interleaved the user/assistant rows in the
        # messages table, so the next turn read a scrambled history.
        self._thread_locks: dict[str, threading.Lock] = {}
        self._thread_locks_guard = threading.Lock()
        self._ensure_schema()

    def serve_forever(self) -> None:
        if not self.api_key:
            raise ValueError(
                "Runtime API HTTP server requires an api_key; set NEXUSCLI_RUNTIME_API_KEY."
            )
        self._start_workers()

        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                outer._handle(self)

            def do_GET(self) -> None:  # noqa: N802
                outer._handle(self)

            def log_message(self, _format: str, *args: Any) -> None:
                return

        server = _build_http_server(Handler, self.port)
        self._server = server
        bound_port = server.server_address[1]
        print(f"NexusCLI Runtime API listening on http://127.0.0.1:{bound_port}", flush=True)
        if self.workbench:
            print(f"Workbench: http://127.0.0.1:{bound_port}/", flush=True)
        try:
            server.serve_forever()
        finally:
            self._stop.set()

    def work_forever(self) -> None:
        """Run only durable task workers, without exposing the HTTP API."""

        self._start_workers()
        print(f"NexusCLI task worker running with {self.workers} workers", flush=True)
        try:
            while not self._stop.wait(0.5):
                pass
        except KeyboardInterrupt:
            self._stop.set()

    def _start_workers(self) -> None:
        self.task_manager.requeue_expired()
        for index in range(self.workers):
            worker_id = f"{os.getpid()}-{index}"
            thread = threading.Thread(
                target=self._worker_loop,
                args=(worker_id,),
                name=f"nexuscli-task-{index}",
                daemon=True,
            )
            thread.start()

    def _handle(self, request: BaseHTTPRequestHandler) -> None:
        method = request.command
        path = request.path.split("?", 1)[0]
        # 静态资源（仅 --workbench）在鉴权门之前分流：构建产物不含用户数据，
        # /v1/* 一律不走此分支。
        if method == "GET" and self.workbench and not path.startswith("/v1/"):
            self._serve_static(request, path)
            return
        stream_request = (
            method == "GET" and path.startswith("/v1/threads/") and path.endswith("/events/stream")
        )
        authorized = self._authorized(request) or self._stream_query_authorized(
            request, method, path
        )
        if not authorized:
            _send_json(request, 401, {"error": "unauthorized"})
            return
        if stream_request and not self._stream_origin_allowed(request):
            _send_json(request, 403, {"error": "cross-origin requests are rejected"})
            return
        body = _read_json(request)
        try:
            if method == "POST" and path == "/v1/threads":
                thread_id = self._create_thread()
                _send_json(request, 200, {"id": thread_id})
            elif method == "POST" and path.startswith("/v1/threads/") and path.endswith("/turns"):
                thread_id = path.split("/")[3]
                if not self._thread_exists(thread_id):
                    _send_json(request, 404, {"error": f"thread not found: {thread_id}"})
                    return
                message = str(body.get("message") or body.get("prompt") or "")
                if not message:
                    _send_json(request, 400, {"error": "message is required"})
                    return
                mode = _mode_from_body(body)
                # Serialized per thread: a second turn for the same thread
                # waits instead of interleaving with the first one.
                with self._thread_lock(thread_id):
                    result = asyncio.run(self._run_turn(thread_id, message, mode=mode))
                _send_json(request, 200, result)
            elif stream_request:
                thread_id = path[len("/v1/threads/") : -len("/events/stream")]
                if not self._thread_exists(thread_id):
                    _send_json(request, 404, {"error": f"thread not found: {thread_id}"})
                    return
                query = parse_qs(urlsplit(request.path).query)
                since = _int_query(query, "since", 0)
                self._send_event_stream(request, thread_id, since)
            elif method == "GET" and path == "/v1/sessions":
                query = parse_qs(urlsplit(request.path).query)
                _send_json(
                    request,
                    200,
                    reads.list_sessions(
                        cwd=self.cwd,
                        limit=_int_query(query, "limit", 20),
                        all_projects=(query.get("all") or ["0"])[0] in {"1", "true"},
                    ),
                )
            elif (
                method == "GET"
                and path.startswith("/v1/sessions/")
                and path.endswith("/transcript")
            ):
                session_id = path[len("/v1/sessions/") : -len("/transcript")]
                query = parse_qs(urlsplit(request.path).query)
                result = reads.read_transcript(
                    session_id,
                    offset=_int_query(query, "offset", 0),
                    limit=_int_query(query, "limit", 200),
                )
                if result is None:
                    _send_json(request, 404, {"error": f"session not found: {session_id}"})
                else:
                    _send_json(request, 200, result)
            elif method == "GET" and path == "/v1/usage/summary":
                query = parse_qs(urlsplit(request.path).query)
                _send_json(request, 200, reads.usage_summary(days=_int_query(query, "days", 7)))
            elif method == "GET" and path.startswith("/v1/threads/") and path.endswith("/events"):
                thread_id = path.split("/")[3]
                if not self._thread_exists(thread_id):
                    _send_json(request, 404, {"error": f"thread not found: {thread_id}"})
                    return
                self._send_events(request, thread_id)
            elif method == "POST" and path == "/v1/tasks":
                prompt = str(body.get("message") or body.get("prompt") or "")
                if not prompt:
                    _send_json(request, 400, {"error": "message is required"})
                    return
                mode = _mode_from_body(body)
                task_id = self.task_manager.add(prompt, mode=mode)
                _send_json(request, 200, {"id": task_id, "status": "queued", "mode": mode})
            elif method == "GET" and path == "/v1/tasks":
                _send_json(request, 200, {"tasks": [asdict(t) for t in self.task_manager.list()]})
            elif method == "GET" and path.startswith("/v1/tasks/"):
                task = self.task_manager.get(path.split("/")[3])
                payload = asdict(task) if task else {"error": "not found"}
                _send_json(request, 200 if task else 404, payload)
            elif method == "POST" and path.startswith("/v1/tasks/") and path.endswith("/cancel"):
                task_id = path.split("/")[3]
                _send_json(request, 200, {"canceled": self.task_manager.cancel(task_id)})
            else:
                _send_json(request, 404, {"error": "not found"})
        except ValueError as exc:
            _send_json(request, 400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - API boundary
            _send_json(request, 500, {"error": str(exc)})

    async def _run_turn(
        self, thread_id: str, message: str, *, mode: str = "react"
    ) -> dict[str, Any]:
        self._ensure_llm_key()
        mode = _validate_mode(mode)
        self._append_event(thread_id, "turn.started", {"message": message, "mode": mode})
        registry, _manager = await build_tool_registry(config=self.config, cwd=self.cwd)
        engine = QueryEngine(
            llm_client=create_llm_client(
                self.config.llm,
                telemetry_enabled=self.config.telemetry.enabled,
            ),
            tool_registry=registry,
            config=self.config,
            cwd=self.cwd,
        )
        history = self._thread_history(thread_id) if mode == "react" else []
        text = ""
        events = _engine_events(engine, mode, message, history=history)
        total_tokens = 0
        async for event in events:
            event_type = str(event.get("type"))
            if event_type == "text_delta":
                text += str(event.get("text") or "")
                self._append_event(thread_id, "message.delta", {"text": event.get("text") or ""})
            elif event_type in {"tool_call", "tool_result", "error", "done"}:
                self._append_event(thread_id, event_type, _jsonable(event))
                if event_type == "error":
                    raise event["error"]
                if event_type == "done":
                    total_tokens = int(event.get("total_tokens") or 0)
        self._append_thread_message(thread_id, "user", message)
        self._append_thread_message(thread_id, "assistant", text)
        self._append_event(
            thread_id,
            "turn.completed",
            {"text": text, "mode": mode, "total_tokens": total_tokens},
        )
        return {
            "thread_id": thread_id,
            "text": text,
            "mode": mode,
            "total_tokens": total_tokens,
        }

    def _worker_loop(self, worker_id: str) -> None:
        last_requeue = 0.0
        while not self._stop.is_set():
            try:
                task = self.task_manager.claim_next(worker_id, lease_seconds=300)
            except Exception as exc:  # noqa: BLE001 - workers must survive transient DB errors
                print(f"worker {worker_id}: claim failed: {exc}", flush=True)
                self._stop.wait(1.0)
                continue
            if not task:
                self._stop.wait(0.5)
                # Expired leases are requeued at startup; refresh periodically so
                # stranded tasks recover without a process restart.
                if time.monotonic() - last_requeue > 30:
                    last_requeue = time.monotonic()
                    with suppress(Exception):
                        self.task_manager.requeue_expired()
                continue
            heartbeat_stop = threading.Event()
            heartbeat = threading.Thread(
                target=self._heartbeat_loop,
                args=(task.id, worker_id, heartbeat_stop),
                name=f"nexuscli-heartbeat-{task.id}",
                daemon=True,
            )
            heartbeat.start()
            try:
                result = asyncio.run(self._run_task(task))
                self.task_manager.complete(task.id, result, worker_id=worker_id)
            except TaskCanceledError:
                # cancel() owns the terminal state; never overwrite it from the worker.
                pass
            except Exception as exc:  # noqa: BLE001
                try:
                    self.task_manager.fail(task.id, str(exc), worker_id=worker_id)
                except Exception as fail_exc:  # noqa: BLE001
                    print(f"worker {worker_id}: fail({task.id}) error: {fail_exc}", flush=True)
            finally:
                heartbeat_stop.set()
                heartbeat.join(timeout=1)

    def _heartbeat_loop(
        self,
        task_id: str,
        worker_id: str,
        stop: threading.Event,
    ) -> None:
        while not stop.wait(30):
            try:
                if not self.task_manager.heartbeat(task_id, worker_id, lease_seconds=300):
                    return
            except Exception:  # noqa: BLE001 - a dead heartbeat causes duplicate execution
                continue

    async def _run_task(self, task: TaskRecord) -> str:
        self._ensure_llm_key()
        if self.task_manager.is_canceled(task.id):
            raise TaskCanceledError(task.id)
        registry, _manager = await build_tool_registry(config=self.config, cwd=self.cwd)
        engine = QueryEngine(
            llm_client=create_llm_client(
                self.config.llm,
                telemetry_enabled=self.config.telemetry.enabled,
            ),
            tool_registry=registry,
            config=self.config,
            cwd=self.cwd,
        )
        text = ""
        async for event in _engine_events(engine, task.mode, task.prompt):
            if self.task_manager.is_canceled(task.id):
                raise TaskCanceledError(task.id)
            if event.get("type") == "text_delta":
                text += str(event.get("text") or "")
            elif event.get("type") == "error":
                raise event["error"]
        return text

    def _ensure_llm_key(self) -> None:
        if not self.config.llm.api_key:
            raise ValueError(
                "NEXUSCLI_API_KEY is not configured. Runtime turns/tasks need a working LLM key."
            )

    def _authorized(self, request: BaseHTTPRequestHandler) -> bool:
        if not self.api_key:
            # Fail closed: worker-only mode never serves HTTP, but if an
            # unauthenticated server somehow handles requests, reject them all.
            return False
        auth = request.headers.get("authorization", "")
        token = request.headers.get("x-api-key", "")
        return auth == f"Bearer {self.api_key}" or token == self.api_key

    def _create_thread(self) -> str:
        thread_id = f"thread_{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
        with self._connect() as conn:
            conn.execute(
                "insert into threads(id, created_at) values (?, ?)",
                (thread_id, datetime.now(UTC).isoformat()),
            )
        self._append_event(thread_id, "thread.created", {"id": thread_id})
        return thread_id

    def _thread_exists(self, thread_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("select 1 from threads where id = ?", (thread_id,)).fetchone()
        return row is not None

    def _thread_lock(self, thread_id: str) -> threading.Lock:
        with self._thread_locks_guard:
            return self._thread_locks.setdefault(thread_id, threading.Lock())

    def _append_event(self, thread_id: str, event_type: str, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                insert into events(thread_id, type, payload, created_at)
                values (?, ?, ?, ?)
                """,
                (
                    thread_id,
                    event_type,
                    json.dumps(payload, ensure_ascii=False),
                    datetime.now(UTC).isoformat(),
                ),
            )
        with self._event_cond:
            self._event_cond.notify_all()

    def _append_thread_message(self, thread_id: str, role: str, content: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                insert into messages(thread_id, role, content, created_at)
                values (?, ?, ?, ?)
                """,
                (thread_id, role, content, datetime.now(UTC).isoformat()),
            )

    def _thread_history(self, thread_id: str, limit: int = 100) -> list[Message]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select role, content
                from messages
                where thread_id = ?
                order by id desc
                limit ?
                """,
                (thread_id, limit),
            ).fetchall()
        return [Message(role=role, content=content) for role, content in reversed(rows)]

    def _send_events(self, request: BaseHTTPRequestHandler, thread_id: str) -> None:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select type, payload, created_at
                from events
                where thread_id = ?
                order by id
                """,
                (thread_id,),
            ).fetchall()
        body = "".join(
            f"event: {event_type}\ndata: {payload}\n\n" for event_type, payload, _created_at in rows
        ).encode("utf-8")
        request.send_response(200)
        request.send_header("content-type", "text/event-stream")
        request.send_header("content-length", str(len(body)))
        request.end_headers()
        request.wfile.write(body)

    # ------------------------------------------------------------------
    # Workbench extensions (F2/F4/F5): SSE stream, static hosting, reads.
    # ------------------------------------------------------------------

    def _stream_query_authorized(
        self, request: BaseHTTPRequestHandler, method: str, path: str
    ) -> bool:
        """api_key 查询参数只对 SSE stream 路径生效（浏览器 EventSource 带不了自定义头）。"""

        if method != "GET" or not self.api_key:
            return False
        if not path.startswith("/v1/threads/") or not path.endswith("/events/stream"):
            return False
        query = parse_qs(urlsplit(request.path).query)
        supplied = (query.get("api_key") or [""])[0]
        return bool(supplied) and hmac.compare_digest(supplied, self.api_key)

    def _stream_origin_allowed(self, request: BaseHTTPRequestHandler) -> bool:
        origin = request.headers.get("origin")
        if origin is None:
            return True
        return is_loopback_origin(origin)

    def _send_event_stream(
        self, request: BaseHTTPRequestHandler, thread_id: str, since: int
    ) -> None:
        acquired = self._sse_slots.acquire(timeout=0)
        if not acquired:
            request.send_response(503)
            request.send_header("retry-after", "5")
            request.send_header("content-length", "0")
            request.end_headers()
            return
        try:
            request.connection.settimeout(SSE_HEARTBEAT_SECONDS)
            request.send_response(200)
            request.send_header("content-type", "text/event-stream")
            request.send_header("cache-control", "no-store")
            request.send_header("connection", "close")
            request.end_headers()
            last_id = max(0, since)
            while not self._stop.is_set():
                rows = self._events_since(thread_id, last_id)
                if rows:
                    payload = "".join(
                        f"id: {row_id}\nevent: {event_type}\ndata: {row_payload}\n\n"
                        for row_id, event_type, row_payload in rows
                    ).encode("utf-8")
                    request.wfile.write(payload)
                    request.wfile.flush()
                    last_id = rows[-1][0]
                    continue
                with self._event_cond:
                    self._event_cond.wait(SSE_HEARTBEAT_SECONDS)
                if not self._events_since(thread_id, last_id):
                    request.wfile.write(b": ping\n\n")
                    request.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            pass  # 客户端断开/半死：清理退出，finally 释放槽位
        finally:
            self._sse_slots.release()
            with suppress(Exception):
                request.connection.close()

    def _events_since(self, thread_id: str, last_id: int) -> list[tuple[int, str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select id, type, payload
                from events
                where thread_id = ? and id > ?
                order by id
                """,
                (thread_id, last_id),
            ).fetchall()
        return [(int(row[0]), str(row[1]), str(row[2])) for row in rows]

    def _workbench_dist_root(self) -> Path:
        override = os.environ.get("NEXUSCLI_WORKBENCH_DIST")
        if override:
            return Path(override)
        return Path(self.cwd) / "frontend" / "dist"

    def _serve_static(self, request: BaseHTTPRequestHandler, path: str) -> None:
        dist_root = self._workbench_dist_root()
        index = dist_root / "index.html"
        # raw 与 decode 后的路径段里出现 ".." 一律 404：normpath 会把 /a/../../x
        # 折叠成根内路径，让攻击请求落入 SPA 回退而非显式拒绝。
        from urllib.parse import unquote

        if ".." in path.split("/") or ".." in unquote(path).split("/"):
            _send_bytes(request, 404, b"not found", "text/plain; charset=utf-8")
            return
        rel = posixpath.normpath(path).lstrip("/")
        if rel.startswith(".."):
            _send_bytes(request, 404, b"not found", "text/plain; charset=utf-8")
            return
        target: Path | None = None
        if not rel or rel == ".":
            target = index if index.is_file() else None
        else:
            candidate = dist_root / rel
            try:
                resolved = candidate.resolve()
                resolved.relative_to(dist_root.resolve())
            except (ValueError, OSError):
                _send_bytes(request, 404, b"not found", "text/plain; charset=utf-8")
                return
            if resolved.is_file():
                target = resolved
            elif not candidate.suffix:
                target = index if index.is_file() else None
            else:
                _send_bytes(request, 404, b"not found", "text/plain; charset=utf-8")
                return
        if target is None:
            # dist 缺失（F2 交付时 F3 未落地 / wheel 检出形态）：空态页 + 构建指引
            _send_bytes(request, 200, _EMPTY_STATE_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        _send_file(request, target)

    def _ensure_schema(self) -> None:

        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists threads (
                    id text primary key,
                    created_at text not null
                )
                """
            )
            conn.execute(
                """
                create table if not exists messages (
                    id integer primary key autoincrement,
                    thread_id text not null,
                    role text not null,
                    content text not null,
                    created_at text not null
                )
                """
            )
            conn.execute(
                "create index if not exists idx_messages_thread on messages(thread_id, id)"
            )
            conn.execute(
                """
                create table if not exists events (
                    id integer primary key autoincrement,
                    thread_id text not null,
                    type text not null,
                    payload text not null,
                    created_at text not null
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)


def _build_http_server(handler: type[BaseHTTPRequestHandler], port: int) -> ThreadingHTTPServer:
    """Bind the loopback server without serving; port 0 picks a free port (test seam)."""

    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def _int_query(query: dict[str, list[str]], name: str, default: int) -> int:
    raw = (query.get(name) or [str(default)])[0]
    try:
        return int(raw)
    except ValueError:
        return default


_STATIC_CONTENT_TYPES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".html": "text/html",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".map": "application/json",
    ".woff2": "font/woff2",
    ".wasm": "application/wasm",
}


def _content_type_for(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix in _STATIC_CONTENT_TYPES:
        ctype = _STATIC_CONTENT_TYPES[suffix]
    else:
        guessed = mimetypes.guess_type(name)[0] or "application/octet-stream"
        ctype = guessed
    if ctype.startswith(("text/", "application/json")) and "charset" not in ctype:
        ctype = f"{ctype}; charset=utf-8"
    return ctype


def _send_bytes(
    request: BaseHTTPRequestHandler, status: int, body: bytes, content_type: str
) -> None:
    request.send_response(status)
    request.send_header("content-type", content_type)
    request.send_header("content-length", str(len(body)))
    request.send_header("cache-control", "no-store")
    request.end_headers()
    request.wfile.write(body)


def _send_file(request: BaseHTTPRequestHandler, path: Path) -> None:
    body = path.read_bytes()
    _send_bytes(request, 200, body, _content_type_for(path.name))


_EMPTY_STATE_HTML = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>NexusCLI Workbench</title></head>
<body>
<h1>NexusCLI 工作台 UI 未构建</h1>
<p>当前部署不包含已构建的前端产物（frontend/dist）。</p>
<p>构建指引：<code>cd frontend &amp;&amp; npm run build</code>（Node.js 20.19+）。</p>
<p><code>/v1/*</code> Runtime API 不受影响，照常可用。</p>
</body>
</html>
"""


def _read_json(request: BaseHTTPRequestHandler) -> dict[str, Any]:
    try:
        length = int(request.headers.get("content-length") or 0)
    except ValueError:
        return {}
    if length == 0:
        return {}
    try:
        value = json.loads(request.rfile.read(length).decode("utf-8"))
    except (json.JSONDecodeError, OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _send_json(request: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request.send_response(status)
    request.send_header("content-type", "application/json")
    request.send_header("content-length", str(len(body)))
    request.end_headers()
    request.wfile.write(body)


def _jsonable(event: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for key, value in event.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = value
        else:
            result[key] = str(value)
    return result


def runtime_api_key(explicit: str | None = None) -> str:
    key = explicit or os.environ.get("NEXUSCLI_RUNTIME_API_KEY")
    if not key:
        raise ValueError("NEXUSCLI_RUNTIME_API_KEY is required for Runtime API")
    return key


def _mode_from_body(body: dict[str, Any]) -> str:
    return _validate_mode(str(body.get("mode") or "react"))


def _validate_mode(mode: str) -> str:
    value = mode.strip().lower()
    if value not in TASK_MODES:
        raise ValueError(f"mode must be one of: {', '.join(sorted(TASK_MODES))}")
    return value


def _engine_events(engine: QueryEngine, mode: str, message: str, *, history=None):
    if mode == "plan":
        return engine.plan(message)
    if mode == "team":
        return engine.team(message)
    return engine.ask(message, history=history)
