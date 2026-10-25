from __future__ import annotations

import asyncio
import hashlib
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
from nexuscli.runtime.approvals import (
    APPROVAL_INPUT_MAX_BYTES,
    ApprovalBridge,
    ApprovalConflictError,
    ApprovalInputTooLargeError,
    UnknownApprovalError,
)
from nexuscli.runtime.tasks import TASK_MODES, DurableTaskManager, TaskRecord
from nexuscli.runtime.websession import (
    RevisionConflict,
    SessionPermissions,
    WebSession,
    cookie_value_from_header,
    session_cookie_header,
)
from nexuscli.types import Message

MAX_SSE_CLIENTS = 16
SSE_HEARTBEAT_SECONDS = 15
# A2 幂等登记表容量（kitagent Gateway._record 同款 1000；满则 429）
IDEMPOTENCY_CAPACITY = 1000

# A1 安全壳 CSP（Ported from kitagent (MIT, zed123214/kitagent)
# src/agent_runtime/web/app.py:125-128）
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
)


class TaskCanceledError(RuntimeError):
    pass


class IdempotencyError(Exception):
    """A2 幂等门的拒绝响应（kitagent GatewayError 的同位形态：status + message）。"""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


class _ResponseCapture(threading.local):
    """A2 首次结果捕获位：`_send_json` 写，`_handle` 的 finally 读。

    线程本地位——ThreadingHTTPServer 每个连接一个线程、连接内请求串行，
    `_handle` 开头复位即可避免上一请求的残留串扰。
    """

    def __init__(self) -> None:
        self.last_json: tuple[int, dict[str, Any]] | None = None


_response_capture = _ResponseCapture()


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
        self.approval_bridge = ApprovalBridge()
        # F14 工作台会话：一次性口令 + HttpOnly cookie + csrf（kitagent 模型）
        self.web_session = WebSession()
        # A6 会话权限模式（Ported from kitagent (MIT, zed123214/kitagent)
        # core/permissions/manager.py:66-108）：仅内存，单会话单实例；执行器
        # 审批回调经 callback_for(permissions=...) 与本实例接线。
        self.session_permissions = SessionPermissions()
        # SSE 客户端槽位与事件唤醒信号（DB 仍是唯一事实源，Condition 只消除轮询延迟）
        self._sse_slots = threading.BoundedSemaphore(MAX_SSE_CLIENTS)
        self._event_cond = threading.Condition()
        # A7 事件续号（Ported from kitagent (MIT, zed123214/kitagent)
        # src/agent_runtime/core/events/bus.py:66-104）：_append_event 是唯一
        # 持久事件汇，在锁内推导线程内单调 seq（自 1 起，跨 turn 不重置），
        # 对应 kitagent bus 持有 _event_seq 计数器的角色。
        self._event_seq_guard = threading.Lock()
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
        # A2 幂等登记表（Ported from kitagent (MIT, zed123214/kitagent)
        # src/agent_runtime/web/gateway.py:72-93）：x-request-id → payload 摘要
        # + 首次响应；Condition 用于在飞重放等待首请求完成。
        self._idempotency: dict[str, dict[str, Any]] = {}
        self._idempotency_cond = threading.Condition()
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

            def do_PATCH(self) -> None:  # noqa: N802
                outer._handle(self)

            def log_message(self, _format: str, *args: Any) -> None:
                return

        server = _build_http_server(Handler, self.port)
        self._server = server
        bound_port = server.server_address[1]
        print(f"NexusCLI Runtime API listening on http://127.0.0.1:{bound_port}", flush=True)
        if self.workbench:
            print(f"Workbench: http://127.0.0.1:{bound_port}/", flush=True)
            print(
                f"打开一键链接完成认证: http://127.0.0.1:{bound_port}/#bootstrap="
                f"{self.web_session.bootstrap_token}",
                flush=True,
            )
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
        # A2 捕获位复位：同一连接线程串行处理多个请求，清掉上一请求的残留。
        _response_capture.last_json = None
        method = request.command
        path = request.path.split("?", 1)[0]
        # A1 前置门（Ported from kitagent (MIT, zed123214/kitagent)
        # src/agent_runtime/web/app.py:106-128 的请求体门）：非 GET 请求体必须
        # 有界（> 64 KiB → 413）且为 application/json（→ 415）；bootstrap 端点
        # 同样受限（门在凭证判断之前）。无 content-length 的无体请求按 0 处理
        # 照旧放行，保持既有零适配行为。
        if method != "GET":
            length = request.headers.get("content-length", "")
            if length.isdigit() and int(length) > 65536:
                _send_json(request, 413, {"error": "request body must stay below 64 KiB"})
                return
            if (
                length.isdigit()
                and int(length)
                and request.headers.get("content-type", "").split(";")[0] != "application/json"
            ):
                _send_json(request, 415, {"error": "request body must be application/json"})
                return
        # 静态资源（仅 --workbench）在鉴权门之前分流：构建产物不含用户数据，
        # /v1/* 一律不走此分支。
        if method == "GET" and self.workbench and not path.startswith("/v1/"):
            self._serve_static(request, path)
            return
        # F14 bootstrap：一次性口令本身即凭证，免 cookie/key 门。
        if method == "POST" and path == "/v1/workbench/bootstrap":
            self._handle_bootstrap(request)
            return
        # F14 Origin 白名单：浏览器跨源请求必带 Origin 且非本机来源 → 403
        # （先于凭证判断——无凭证也拒绝跨源探测）。无 Origin = curl 等非浏览器
        # 客户端，走凭证门。
        origin = request.headers.get("origin")
        if origin is not None and not is_loopback_origin(origin):
            _send_json(request, 403, {"error": "cross-origin requests are rejected"})
            return
        stream_request = (
            method == "GET" and path.startswith("/v1/threads/") and path.endswith("/events/stream")
        )
        # F14 会话门三通道：api_key/Bearer 头（外部自动化）、api_key 查询参数
        # （仅 SSE）、HttpOnly cookie（浏览器，经 bootstrap 换发）。
        header_authorized = self._authorized(request)
        cookie_authorized = self.web_session.authorize(
            cookie_value_from_header(request.headers.get("cookie"))
        )
        if (
            not header_authorized
            and not cookie_authorized
            and not self._stream_query_authorized(request, method, path)
        ):
            _send_json(request, 401, {"error": "unauthorized"})
            return
        # F14 CSRF：cookie 通道的变更请求必须带 x-csrf-token（api_key 通道豁免——
        # 外部程序无 cookie，凭证本身就是凭据）。A6 的 PATCH /v1/workbench/
        # permissions 同为变更请求，与 POST 一并纳入。
        if (
            cookie_authorized
            and not header_authorized
            and method in ("POST", "PATCH")
            and not self.web_session.csrf_matches(request.headers.get("x-csrf-token"))
        ):
            _send_json(request, 403, {"error": "csrf token missing or invalid"})
            return
        if stream_request and not self._stream_origin_allowed(request):
            _send_json(request, 403, {"error": "cross-origin requests are rejected"})
            return
        body = _read_json(request)
        # A2 幂等门（Ported from kitagent (MIT, zed123214/kitagent)
        # src/agent_runtime/web/gateway.py:72-93 的 Gateway._record）：POST 带
        # x-request-id 才进门（缺失则零适配直通）。同 ID 同 payload 摘要 → 重放
        # 首次结果；同 ID 异 payload → 409；首见 → 登记摘要放行，dispatch 结束后
        # 回填首次结果；登记表满（1000）→ 429。bootstrap 在此之前已分流，不在
        # 幂等门内（kitagent 同款：bootstrap 一次性语义本身不幂等）。
        idem_key: str | None = None
        idem_fresh = False
        if method == "POST":
            idem_key = request.headers.get("x-request-id")
            if idem_key:
                try:
                    replay = self._idempotency_gate(idem_key, _payload_digest(path, body))
                except IdempotencyError as exc:
                    _send_json(request, exc.status, {"error": str(exc)})
                    return
                if replay is not None:
                    replay_status, replay_payload = replay
                    _send_json(request, replay_status, replay_payload)
                    return
                idem_fresh = True
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
            elif method == "GET" and path == "/v1/approvals":
                _send_json(request, 200, {"approvals": self.approval_bridge.list_pending()})
            elif (
                method == "POST"
                and path.startswith("/v1/approvals/")
                and path.endswith("/decision")
            ):
                approval_id = path[len("/v1/approvals/") : -len("/decision")]
                decision = str(body.get("decision") or "")
                if decision not in ("approve", "deny"):
                    _send_json(request, 400, {"error": "decision must be approve or deny"})
                    return
                origin = request.headers.get("origin")
                if origin is not None and not is_loopback_origin(origin):
                    _send_json(request, 403, {"error": "cross-origin requests are rejected"})
                    return
                # A5 五态决策（Ported from kitagent (MIT, zed123214/kitagent)
                # web/session.py:183-232）：pending 落定；同动词重复决策幂等重放
                # 首次结果；异动词翻转 / expired / unknown 拒绝变更；参数截断禁
                # allow。类型化错误 → 状态码映射见各异常。
                try:
                    resolved = self.approval_bridge.decide(approval_id, decision)  # type: ignore[arg-type]
                except UnknownApprovalError:
                    _send_json(request, 404, {"error": f"approval not found: {approval_id}"})
                except ApprovalConflictError:
                    # 与既有口径一致：不区分从未存在与已解决（F6 回归锚）。
                    _send_json(
                        request,
                        404,
                        {"error": f"approval not found or already resolved: {approval_id}"},
                    )
                except ApprovalInputTooLargeError as exc:
                    _send_json(
                        request,
                        413,
                        {
                            "error": str(exc),
                            "code": "approval_input_too_large",
                            "size": exc.size,
                            "max_bytes": APPROVAL_INPUT_MAX_BYTES,
                        },
                    )
                else:
                    _send_json(
                        request,
                        200,
                        {
                            "ok": True,
                            "id": approval_id,
                            "resolved_by": resolved.resolved_by,
                            "decision": decision,
                            "status": resolved.status,
                        },
                    )
            elif method == "GET" and path == "/v1/workbench/permissions":
                # A6 会话权限读面（kitagent session.get_permissions rpc 同位，
                # web/session.py:228）：客户端据此取 expected_revision。
                _send_json(request, 200, self.session_permissions.snapshot())
            elif method == "PATCH" and path == "/v1/workbench/permissions":
                # A6 权限变更（Ported from kitagent (MIT, zed123214/kitagent)
                # core/permissions/manager.py:66-108 configure_session）：revision
                # 乐观锁——不匹配 → 409（客户端刷新后重试）；mode 切 read_only/
                # auto 清空授权；revoke_tool 单项撤销。变更成功后即时重判全部
                # 挂起审批（manager.py:99-108 _recheck_pending 同款）。
                expected = body.get("expected_revision")
                if isinstance(expected, bool) or not isinstance(expected, int):
                    _send_json(request, 400, {"error": "expected_revision (int) is required"})
                    return
                mode = body.get("mode")
                if mode is not None and not isinstance(mode, str):
                    _send_json(request, 400, {"error": "mode must be a string"})
                    return
                revoke_tool = body.get("revoke_tool")
                if revoke_tool is not None and (
                    not isinstance(revoke_tool, str) or not revoke_tool
                ):
                    _send_json(request, 400, {"error": "revoke_tool must be a non-empty string"})
                    return
                try:
                    state = self.session_permissions.configure(
                        expected_revision=expected,
                        mode=mode,  # type: ignore[arg-type]
                        revoke_tool=revoke_tool,
                    )
                except RevisionConflict as exc:
                    _send_json(
                        request,
                        409,
                        {"error": str(exc), "current_revision": exc.current_revision},
                    )
                except ValueError as exc:
                    _send_json(request, 400, {"error": str(exc)})
                else:
                    rechecked = self.approval_bridge.recheck_pending(self.session_permissions)
                    _send_json(request, 200, {**state, "rechecked": rechecked})
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
        finally:
            # A2：首见登记的请求无论走哪个出口（分支响应 / 400 / 500）都把本次
            # 首次结果回填登记表，供同 ID 重放。重放与 409/429 不登记（fresh=False）。
            if idem_fresh and idem_key is not None:
                self._idempotency_complete(idem_key, _response_capture.last_json)

    def _handle_bootstrap(self, request: BaseHTTPRequestHandler) -> None:
        body = _read_json(request)
        csrf = self.web_session.bootstrap(str(body.get("token") or ""))
        if csrf is None:
            _send_json(request, 403, {"error": "bootstrap token invalid or already used"})
            return
        body_bytes = json.dumps({"csrf": csrf}, ensure_ascii=False).encode("utf-8")
        request.send_response(200)
        request.send_header("content-type", "application/json")
        request.send_header("content-length", str(len(body_bytes)))
        request.send_header("set-cookie", session_cookie_header(self.web_session.cookie))
        _apply_security_headers(request)
        request.end_headers()
        request.wfile.write(body_bytes)

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
            approval_callback=self.approval_bridge.callback_for(
                thread_id=thread_id, permissions=self.session_permissions
            ),
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
            approval_callback=self.approval_bridge.callback_for(
                task_id=task.id, permissions=self.session_permissions
            ),
        )
        text = ""
        async for event in _engine_events(engine, task.mode, task.prompt, run_id=task.run_id):
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

    def _idempotency_gate(self, request_id: str, digest: str) -> tuple[int, dict[str, Any]] | None:
        """A2 幂等登记门（Ported from kitagent (MIT, zed123214/kitagent)
        src/agent_runtime/web/gateway.py:72-93 的 Gateway._record）。

        返回 None = 首见（已登记，放行执行）；返回 (status, payload) = 重放首次
        结果；同 ID 异 payload 抛 409；登记表满抛 429。首请求在飞时，同 ID 重放
        在 Condition 上等它完成（kitagent 语义：绝不重复执行）。
        """

        with self._idempotency_cond:
            entry = self._idempotency.get(request_id)
            if entry is not None:
                if entry["digest"] != digest:
                    raise IdempotencyError(409, "same x-request-id with different payload")
                while not entry["done"]:
                    self._idempotency_cond.wait()
                response = entry["response"]
                if response is None:  # 防御：首请求未产出响应即终止，绝不重执行
                    return 500, {"error": "first request did not produce a response"}
                return response
            if len(self._idempotency) >= IDEMPOTENCY_CAPACITY:
                raise IdempotencyError(429, "idempotency registry is full")
            self._idempotency[request_id] = {"digest": digest, "done": False, "response": None}
            return None

    def _idempotency_complete(
        self, request_id: str, response: tuple[int, dict[str, Any]] | None
    ) -> None:
        """回填首次结果并唤醒在飞重放（对应 kitagent record 状态收敛的位置）。"""

        with self._idempotency_cond:
            entry = self._idempotency.get(request_id)
            if entry is None or entry["done"]:
                return
            entry["response"] = response or (500, {"error": "internal error before response"})
            entry["done"] = True
            self._idempotency_cond.notify_all()

    def _append_event(self, thread_id: str, event_type: str, payload: dict[str, Any]) -> None:
        """写入一条线程事件，并在 payload 内盖线程内单调 ``seq`` 后落库。

        A7 事件续号（Ported from kitagent (MIT, zed123214/kitagent)
        src/agent_runtime/core/events/bus.py:66-104 的 EventBus.publish /
        resume_event_seq 语义）：

        - 同一 thread 每个事件在写入 events 表时获得 ``seq``：自 1 起、严格
          +1；turn 边界（turn.started）不重置——线程生命周期单调（对应
          kitagent bus 对同 correlation/run 事件的单调分配）。
        - 续号游标每次落库前从 events 表该线程的持久行数推导，等价于
          kitagent ``resume_event_seq``：换实例/重启后下一个事件自动越过
          已持久化游标继续编号，不需要显式调用。
        - 选 payload 内增字段而非复用 events 自增 id：id 是全库共享游标
          （非每线程自 1 起，且与 SSE ``id:`` 重放游标重复）；per-thread
          seq 才对齐 kitagent 的 per-run 计数，也便于前端做丢帧检测。
          SSE ``data:`` 就是落库 payload 原文，seq 随之透出（前端不读无影响）。
        - 事件类型集不变；调用方 payload 若自带 ``seq`` 以落库游标为准
          （覆盖写入，sink 权威，对应 bus 对 event_seq 的收口位置）。
        """

        with self._event_seq_guard, self._connect() as conn:
            row = conn.execute(
                "select count(*) from events where thread_id = ?", (thread_id,)
            ).fetchone()
            stored = dict(payload)
            stored["seq"] = int(row[0]) + 1
            conn.execute(
                """
                insert into events(thread_id, type, payload, created_at)
                values (?, ?, ?, ?)
                """,
                (
                    thread_id,
                    event_type,
                    json.dumps(stored, ensure_ascii=False),
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
        _apply_security_headers(request)
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
            _apply_security_headers(request)
            request.end_headers()
            return
        try:
            request.connection.settimeout(SSE_HEARTBEAT_SECONDS)
            request.send_response(200)
            request.send_header("content-type", "text/event-stream")
            request.send_header("cache-control", "no-store")
            request.send_header("connection", "close")
            _apply_security_headers(request, no_store_sent=True)
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


def _apply_security_headers(
    request: BaseHTTPRequestHandler, *, no_store_sent: bool = False
) -> None:
    """A1 安全壳：所有响应统一追加（Ported from kitagent (MIT, zed123214/kitagent)
    src/agent_runtime/web/app.py:121-128 的响应头段）。

    响应已自带 cache-control: no-store 时不再重复追加（no_store_sent 由各
    发送点如实传入）。
    """

    if not no_store_sent:
        request.send_header("Cache-Control", "no-store")
    request.send_header("X-Content-Type-Options", "nosniff")
    request.send_header("Referrer-Policy", "no-referrer")
    request.send_header("Content-Security-Policy", _CSP)


def _payload_digest(path: str, body: dict[str, Any]) -> str:
    """A2 摘要（kitagent Gateway._record 的 [操作, payload] 规范化形态）。

    端点路径参与摘要：同一 request-id 打到不同端点视为异 payload（409）。
    """

    return hashlib.sha256(json.dumps([path, body], sort_keys=True).encode()).hexdigest()


def _send_bytes(
    request: BaseHTTPRequestHandler, status: int, body: bytes, content_type: str
) -> None:
    request.send_response(status)
    request.send_header("content-type", content_type)
    request.send_header("content-length", str(len(body)))
    request.send_header("cache-control", "no-store")
    _apply_security_headers(request, no_store_sent=True)
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
    # A2 捕获点：幂等门首见请求的首次结果从这里带回 _handle 的 finally（先记
    # 后发——写响应半途断连时，登记表里仍是真实执行结果）。
    if _response_capture.last_json is None:
        _response_capture.last_json = (status, payload)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request.send_response(status)
    request.send_header("content-type", "application/json")
    request.send_header("content-length", str(len(body)))
    _apply_security_headers(request)
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


def _engine_events(
    engine: QueryEngine, mode: str, message: str, *, history=None, run_id: str | None = None
):
    if mode == "plan":
        return engine.plan(message)
    if mode == "team":
        return engine.team(message)
    if run_id:
        return engine.ask(message, history=history, run_id=run_id)
    return engine.ask(message, history=history)
