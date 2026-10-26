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
from dataclasses import fields as dataclass_fields
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import nexuscli.runtime.approvals as approvals_module
from nexuscli.agent import QueryEngine
from nexuscli.agent.agent import Agent
from nexuscli.agent.turn_checkpoint import TurnCheckpointStore, _checkpoint_id_for_run
from nexuscli.bootstrap import build_tool_registry
from nexuscli.config import NexusCliConfig
from nexuscli.llm import create_llm_client
from nexuscli.net_guards import is_loopback_origin
from nexuscli.prompt import PromptAssembler
from nexuscli.runtime import reads
from nexuscli.runtime.approvals import (
    APPROVAL_INPUT_MAX_BYTES,
    ApprovalBridge,
    ApprovalConflictError,
    ApprovalInputTooLargeError,
    ApprovalSuspension,
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
from nexuscli.tools.base import ToolContext
from nexuscli.tools.executor import ToolExecutor
from nexuscli.types import Message, Usage

MAX_SSE_CLIENTS = 16
SSE_HEARTBEAT_SECONDS = 15
# A10 token 合帧窗口（Ported from kitagent (MIT, zed123214/kitagent)
# src/agent_runtime/web/session.py:69-73：llm.token 首帧起 50ms call_later 延迟
# flush，其余事件立即 flush——同窗内到达的 token 帧合并为单帧推送）。
SSE_DELTA_FLUSH_SECONDS = 0.05
# A10 单个合帧的文本体量上限（字符，防御值）：超大积压一次性并入时提前冲刷，
# 避免产出单帧 MB 级 payload 拖住写循环。
SSE_DELTA_MAX_CHARS = 65536
# A10 单连接积压高水位：单次轮询拉到的非 delta 行超过此值 → 下发 resync 语义帧
# 并整段跳过（kitagent session.py:82-89 订阅队列 maxsize=64 满则丢弃+resync 的
# 同位防护；此处无独立消费队列，取宽松值避免正常 turn 误触发）。
SSE_BACKLOG_HIGH_WATER = 256
# A10 审批登记表快照轮询间隔（approvals 是内存表非 events 行，机制取舍见
# _stream_events docstring）。
SSE_APPROVAL_POLL_SECONDS = 0.1
# A2 幂等登记表容量（kitagent Gateway._record 同款 1000；满则 429）
IDEMPOTENCY_CAPACITY = 1000

# A1 安全壳 CSP（Ported from kitagent (MIT, zed123214/kitagent)
# src/agent_runtime/web/app.py:125-128）
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
)

# A14 端点清单事实源：_handle 各分流分支（静态 → bootstrap → if/elif 链）的机器
# 可读镜像，条目 = (method, path_pattern, auth, summary)。约束：
# 1. 清单与 _handle 的路由分发一一对应——新增/改动分支必须同步这里（既有
#    runtime 测试是分发行为的回归锚）；
# 2. docs/runtime-api.md 的端点表节由 scripts/generate_endpoint_docs.py 对本
#    常量做 ast 解析后生成（Do not edit manually），scripts/check_endpoint_docs.py
#    在 CI 校验同步。分发逻辑本身不消费本清单。
ROUTE_MANIFEST: tuple[tuple[str, str, str, str], ...] = (
    (
        "GET",
        "/",
        "免鉴权（仅 --workbench）",
        "工作台静态资源 / SPA 回退 / 空态页（匹配非 /v1/* 的 GET）",
    ),
    (
        "POST",
        "/v1/workbench/bootstrap",
        "一次性口令",
        "口令一次性换发 HttpOnly cookie 会话与 x-csrf-token",
    ),
    (
        "POST",
        "/v1/workbench/reissue-csrf",
        "cookie 会话",
        "凭有效会话 cookie 取回当前 x-csrf-token（无 cookie 401；不轮换，多标签页收敛）",
    ),
    (
        "POST",
        "/v1/threads",
        "api_key / cookie+csrf",
        "创建线程，返回 {id}",
    ),
    (
        "POST",
        "/v1/threads/{thread_id}/turns",
        "api_key / cookie+csrf",
        "同步执行一个 turn（mode=react/plan/team），完成前阻塞",
    ),
    (
        "GET",
        "/v1/threads/{thread_id}/events/stream",
        "api_key / cookie / api_key 查询参数",
        "SSE 事件流：回放 + 实时 + 15s 心跳（?since=N 续传，16 连接上限）",
    ),
    (
        "GET",
        "/v1/sessions",
        "api_key / cookie",
        "REPL 会话列表（?limit、?all 查询参数，按 cwd 过滤）",
    ),
    (
        "GET",
        "/v1/sessions/{session_id}/transcript",
        "api_key / cookie",
        "会话转录分页读取（?offset、?limit 查询参数）",
    ),
    (
        "GET",
        "/v1/usage/summary",
        "api_key / cookie",
        "用量聚合汇总（?days 查询参数）",
    ),
    (
        "GET",
        "/v1/threads/{thread_id}/events",
        "api_key / cookie",
        "线程事件全量列表（text/event-stream 帧文本，按 id 升序）",
    ),
    (
        "GET",
        "/v1/approvals",
        "api_key / cookie",
        "挂起审批列表",
    ),
    (
        "POST",
        "/v1/approvals/{approval_id}/decision",
        "api_key / cookie+csrf",
        "审批决策（decision=approve/deny，同动词幂等重放）",
    ),
    (
        "GET",
        "/v1/workbench/permissions",
        "api_key / cookie",
        "会话权限快照（含 expected_revision）",
    ),
    (
        "PATCH",
        "/v1/workbench/permissions",
        "api_key / cookie+csrf",
        "会话权限变更（expected_revision 乐观锁，即时重判挂起审批）",
    ),
    (
        "POST",
        "/v1/tasks",
        "api_key / cookie+csrf",
        "创建后台任务（mode=react/plan/team）",
    ),
    (
        "GET",
        "/v1/tasks",
        "api_key / cookie",
        "后台任务列表",
    ),
    (
        "GET",
        "/v1/tasks/{task_id}",
        "api_key / cookie",
        "后台任务详情",
    ),
    (
        "POST",
        "/v1/tasks/{task_id}/cancel",
        "api_key / cookie+csrf",
        "取消后台任务（运行中取消不被迟到结果覆盖）",
    ),
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
        # A9 任务路径事件台账（仅内存，进程生命周期）：任务没有线程事件面（events
        # 表以 thread_id 为键，读路径带 threads 存在性门），react 任务的挂起/收口
        # 信号落在这里供工作台与测试观测。审批登记表同取向：随进程消亡，无落盘。
        self._task_events: dict[str, list[dict[str, Any]]] = {}
        self._task_events_guard = threading.Lock()
        # A10 审批收口台账（仅内存，进程生命周期）：approval_resolved SSE 帧的
        # 数据源。登记点是 api.py 内全部收口调用点（decide 端点 / recheck_pending
        # / expire / cancel_pending）——即 kitagent session.approve/_decide 里
        # projection.put 的同位收口位置。例外：F6 同步桥回调内的 300s 超时收口
        # 发生在 approvals.py 内部（callback 闭包），api.py 看不到，SSE 侧对该
        # 路径以 record_status 兜底（decision/resolved_by 置空，见
        # _emit_approval_frames）。
        self._approval_resolutions: list[dict[str, Any]] = []
        self._approval_resolutions_guard = threading.Lock()
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
            bootstrap_url = (
                f"http://127.0.0.1:{bound_port}/#bootstrap={self.web_session.bootstrap_token}"
            )
            print(f"打开一键链接完成认证: {bootstrap_url}", flush=True)
            self._write_workbench_link_file(bootstrap_url)
        try:
            server.serve_forever()
        finally:
            self._stop.set()

    def _write_workbench_link_file(self, bootstrap_url: str) -> None:
        """workbench 一键链接落盘（2026-10-26_workbench-kitagent-ui task_0006）。

        动机：口令一次性，而服务常被后台/他人终端拉起，用户翻不到启动日志——把
        当前实例的一键链接写进状态目录，gate 页与用户都有可执行的恢复路径。文件
        权限 0600（内嵌一次性口令，等同凭证）；每次启动覆盖（口令本就随进程重生）。
        落盘失败仅提示，不阻断服务启动。
        """
        try:
            link_path = Path.home() / ".nexuscli" / "workbench-link.txt"
            link_path.parent.mkdir(parents=True, exist_ok=True)
            # os.open 直接以 0600 创建（复审 F7）：write-then-chmod 会在 umask 权限下
            # 留出含一次性口令的弱权限窗口。
            fd = os.open(link_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(f"{bootstrap_url}\n")
        except OSError as exc:
            print(f"一键链接落盘失败（仅影响找回入口，不影响认证）: {exc}", flush=True)

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
        # U2 csrf 重签：会话门（401）之后、CSRF 门（403）之前分流——来重签的
        # 客户端本就没有有效 csrf，过不了 403 门。只认 cookie 会话通道：api_key
        # 头通道对此端点无意义（换签的信任根是会话 cookie，无 cookie 一律 401
        # 且不泄露任何信息）。幂等门不适用：本分支在其之前分流，重签无幂等
        # 登记副作用。
        if method == "POST" and path == "/v1/workbench/reissue-csrf":
            if not cookie_authorized:
                _send_json(request, 401, {"error": "unauthorized"})
                return
            csrf = self.web_session.reissue(cookie_value_from_header(request.headers.get("cookie")))
            if csrf is None:  # 防御：与 authorize 同一比对口径，理论不可达
                _send_json(request, 401, {"error": "unauthorized"})
                return
            # 响应不设新 cookie：会话 cookie 不变，只取回 csrf。不轮换（多标签页
            # 收敛：第二标签页冷加载重签不得踢掉已打开标签页，websession.reissue）。
            _send_json(request, 200, {"csrf": csrf})
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
                    # A9 恢复触发：挂起任务的 worker 正等在这张卡的 event 上，
                    # decide() 的 event.set() 即恢复信号——决策端点在此立即返回，
                    # 不做任何恢复工作（HTTP 响应零阻塞）；worker 醒来后按
                    # checkpoint_id 重派 pending 调用并接续任务。
                    suspension = self.approval_bridge.suspension_for(approval_id)
                    payload: dict[str, Any] = {
                        "ok": True,
                        "id": approval_id,
                        "resolved_by": resolved.resolved_by,
                        "decision": decision,
                        "status": resolved.status,
                    }
                    if suspension is not None:
                        payload["suspended_task_id"] = suspension["task_id"]
                        payload["checkpoint_id"] = suspension["checkpoint_id"]
                    # A10：先记后发——对 HTTP 响应作出反应的客户端（如重连 SSE）
                    # 一定能看到这条收口台账（approval_resolved 帧的数据源）。
                    self._record_approval_resolution(
                        approval_id, decision, resolved.resolved_by, resolved.status
                    )
                    _send_json(request, 200, payload)
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
                    # A10：即时重判的收口同样入台账（decision 为 approve/deny，
                    # resolved_by=session_permissions）。
                    for settled in rechecked:
                        self._record_approval_resolution(
                            str(settled["id"]),
                            str(settled["decision"]),
                            settled["resolved_by"],
                            "resolved",
                        )
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
        if task.mode == "react":
            # A9 挂起即 checkpoint：react 任务的审批从「回调阻塞等待」改为登记后
            # 立即中断 + 回合 checkpoint + 决策后恢复（_run_task_react）。
            return await self._run_task_react(task)
        # plan/team 维持 F6 同步桥（无回合 checkpoint/按 id 重派面，挂起恢复 machinery
        # 是 react 特有）：行为与 A9 之前一致，零回归。
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

    def _react_system_prompt(self, registry, llm_client) -> str:
        return PromptAssembler(
            config=self.config,
            cwd=self.cwd,
            tool_names=registry.list_names(),
            model=llm_client.model_name,
            provider=llm_client.provider_name,
        ).build_static()

    def _session_permissions_revision(self) -> int | None:
        snapshot = self.session_permissions.snapshot()
        revision = snapshot.get("revision") if isinstance(snapshot, dict) else None
        return revision if isinstance(revision, int) else None

    async def _run_task_react(self, task: TaskRecord) -> str:
        """A9 react 任务：审批请求 → 登记即挂起（checkpoint 落盘）→ 等决策 → 恢复。

        - 挂起模式只由本路径的调用参数触发（callback_for(suspend=True)）；REPL
          线程路径（_run_turn）与 plan/team 任务仍用 F6 同步桥，回调行为零变化。
        - react 段直接构造 Agent 而非经 QueryEngine.ask：query_engine.py 不在本次
          改动清单内且 ask() 无法传 turn_checkpoints=True，而 checkpoint (a)
          （assistant tool_use 已入 messages、分发未开始）必须先于工具分发落盘，
          挂起时它就是恢复依据（agent.py:522-535 的既有双快照点）。
        - 恢复执行体取舍（设计裁决）：恢复在本 worker 原地执行——既不在决策线程
          直跑，也不二次入队。租约归属把任务钉在认领它的 worker 上（heartbeat/
          complete 都按 worker_id 结算，tasks.py 不在改动清单内，转移所有权需要
          新的管理器原语）；决策端点只 decide() + event.set()，HTTP 响应零阻塞，
          worker 醒来后从 checkpoint 重派。worker 在挂起等待期间仍由既有心跳线程
          保活租约（F6 验证过的机制，_worker_loop 零改动）。
        """

        store = TurnCheckpointStore()
        checkpoint_id = _checkpoint_id_for_run(task.run_id)
        llm_client = create_llm_client(
            self.config.llm,
            telemetry_enabled=self.config.telemetry.enabled,
        )
        try:
            registry, _manager = await build_tool_registry(config=self.config, cwd=self.cwd)
            system_prompt = self._react_system_prompt(registry, llm_client)
            callback = self.approval_bridge.callback_for(
                task_id=task.id, permissions=self.session_permissions, suspend=True
            )
            agent = Agent(
                llm_client=llm_client,
                tool_registry=registry,
                system_prompt=system_prompt,
                cwd=self.cwd,
                config=self.config,
                approval_callback=callback,
                max_turns=self.config.agent.max_turns,
                use_journal=True,
                run_id=task.run_id,
                turn_checkpoints=True,
            )
            text, suspension = await self._consume_react_events(task, agent.run(task.prompt))
            while suspension is not None:
                self.approval_bridge.attach_suspension(
                    suspension.approval_id,
                    task_id=task.id,
                    checkpoint_id=checkpoint_id,
                    permissions_revision=self._session_permissions_revision(),
                )
                self._append_task_event(
                    task.id, {**suspension.as_event(), "checkpoint_id": checkpoint_id}
                )
                decision, approval = self._await_suspension_settlement(task, suspension)
                self.approval_bridge.drop_suspension(suspension.approval_id)
                self._append_task_event(
                    task.id,
                    {
                        "type": "approval_settled",
                        "approval_id": approval.id,
                        "decision": decision,
                        "resolved_by": approval.resolved_by,
                        "tool_name": approval.request.get("tool_name"),
                    },
                )
                if decision != "approve":
                    # deny / 超时 expired：合成拒绝说明、任务正常收口，不重放任何调用。
                    tool_name = str(approval.request.get("tool_name") or "unknown")
                    note = (
                        f'\n\nTool "{tool_name}" was denied by approval policy '
                        f"(resolved_by={approval.resolved_by or 'timeout'}); the task "
                        "finished without executing it."
                    )
                    with suppress(Exception):
                        store.delete(checkpoint_id)
                    return text + note
                resumed_text, suspension = await self._resume_react_from_checkpoint(
                    task, llm_client, registry, system_prompt, callback, store
                )
                text += resumed_text
            with suppress(Exception):
                store.delete(checkpoint_id)
            return text
        except BaseException:
            # 任务到终态（含取消/硬失败）即清回合 checkpoint：无可恢复物，/turn-resume
            # 不该看到终态任务的残影。worker 进程死亡（租约过期重跑）不走这里，
            # checkpoint 留存，重跑会在同一审批上重新挂起新卡。
            with suppress(Exception):
                store.delete(checkpoint_id)
            raise

    async def _consume_react_events(
        self, task: TaskRecord, events: Any
    ) -> tuple[str, ApprovalSuspension | None]:
        """消费一段 react 事件流：返回累计文本；挂起时返回 ApprovalSuspension。"""

        text = ""
        try:
            async for event in events:
                if self.task_manager.is_canceled(task.id):
                    raise TaskCanceledError(task.id)
                if event.get("type") == "text_delta":
                    text += str(event.get("text") or "")
                elif event.get("type") == "error":
                    raise event["error"]
        except ApprovalSuspension as suspension:
            return text, suspension
        return text, None

    def _await_suspension_settlement(
        self, task: TaskRecord, suspension: ApprovalSuspension
    ) -> tuple[str, Any]:
        """同步等待决策收口（worker 线程内，事件循环在此空闲）。

        - decide/recheck_pending/expire 都以 event.set 唤醒；唤醒后决策位即现状。
        - 300s 无决策 → expired（沿用 F6 fail-closed 口径）→ 按 deny 收口。
        - 任务取消：撤销未决卡并抛 TaskCanceledError（cancel() 拥有终态）。
        - 心跳保活：_worker_loop 的心跳线程在 _run_task 返回前一直运行，挂起等待
          期间租约不失效（F6 验证过的机制，零改动复用）。
        """

        approval = suspension.approval
        deadline = time.monotonic() + approvals_module.APPROVAL_TIMEOUT_SECONDS
        while True:
            if self.task_manager.is_canceled(task.id):
                self.approval_bridge.cancel_pending(approval.id)
                # A10：撤销也入台账（卡片凭空消失必须有帧解释；expired 卡
                # decision 保持 None，kitagent session.py:216-217 同款）。
                self._record_approval_resolution(approval.id, None, "task_canceled", "expired")
                raise TaskCanceledError(task.id)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.approval_bridge.expire(approval.id)
                self._record_approval_resolution(approval.id, None, "timeout", "expired")
                return "deny", approval
            if approval.event.wait(timeout=min(0.5, remaining)):
                return approval.decision or "deny", approval

    async def _resume_react_from_checkpoint(
        self,
        task: TaskRecord,
        llm_client: Any,
        registry: Any,
        system_prompt: str,
        callback: Any,
        store: TurnCheckpointStore,
    ) -> tuple[str, ApprovalSuspension | None]:
        """approve 后从回合 checkpoint 恢复：按原 id 重派 pending 调用，再接续本轮。

        journal 分流（F9）保证重派幂等：无记录 → 执行；completed → 复用序列化
        结果（batch 中先于挂起调用已完成的调用不再执行）；started/outcome_unknown
        → fail-closed。刚收口的 approve 决策经桥的持久决策查找（evaluate_durable
        同位）放行同参重放——恢复不会在原审批上二次挂起；若本轮后续再遇新审批，
        同样挂起即 checkpoint（外层 while 循环接续）。
        """

        checkpoint_id = _checkpoint_id_for_run(task.run_id)
        checkpoint = store.load(checkpoint_id)
        if checkpoint is None:
            raise RuntimeError(f"approval resume checkpoint missing: {checkpoint_id}")
        # cwd 归一化守卫（与 /turn-resume 同口径）：异工作区 checkpoint 不接续。
        if os.path.realpath(checkpoint.cwd) != os.path.realpath(self.cwd):
            raise RuntimeError("approval resume checkpoint belongs to a different workspace")
        agent = Agent(
            llm_client=llm_client,
            tool_registry=registry,
            system_prompt=system_prompt,
            cwd=self.cwd,
            config=self.config,
            approval_callback=callback,
            max_turns=self.config.agent.max_turns,
            use_journal=True,
            run_id=checkpoint.run_id,
            turn_checkpoints=True,
        )
        valid_fields = {field.name for field in dataclass_fields(Message)}
        agent.history = [
            Message(**{key: value for key, value in item.items() if key in valid_fields})
            for item in checkpoint.messages
        ]
        agent.last_usage = Usage.from_mapping(checkpoint.usage_so_far)
        agent.last_cost = dict(checkpoint.cost_so_far)
        if checkpoint.pending_tool_calls:
            replay_context = ToolContext(
                cwd=self.cwd,
                config=self.config,
                approval_callback=callback,
                journal=agent._journal,
                run_id=checkpoint.run_id,
            )
            replay_executor = ToolExecutor(registry)
            for call in checkpoint.pending_tool_calls:
                results = await replay_executor.execute_all([call], replay_context)
                for result in results:
                    agent.history.append(
                        Message(
                            role="tool",
                            content=result.content,
                            tool_call_id=result.tool_use_id,
                        )
                    )
        return await self._consume_react_events(task, agent.continue_run())

    def _append_task_event(self, task_id: str, event: dict[str, Any]) -> None:
        with self._task_events_guard:
            self._task_events.setdefault(task_id, []).append(dict(event))

    def task_events(self, task_id: str) -> list[dict[str, Any]]:
        with self._task_events_guard:
            return [dict(event) for event in self._task_events.get(task_id, [])]

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
            self._stream_events(request, thread_id, max(0, since))
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            pass  # 客户端断开/半死：清理退出，finally 释放槽位
        finally:
            self._sse_slots.release()
            with suppress(Exception):
                request.connection.close()

    def _stream_events(self, request: BaseHTTPRequestHandler, thread_id: str, last_id: int) -> None:
        """A10 SSE 推送循环（Ported from kitagent (MIT, zed123214/kitagent)
        src/agent_runtime/web/session.py:60-97 的 event/flush/subscribe：50ms
        合帧 call_later、订阅队列满下发 resync、订阅时高水位捕获无 await）。
        三个机制与取舍：

        1. 审批事件入流（轮询对比，非 Condition 挂钩——ask 二选一）：approvals
           是内存登记表非 events 行。选轮询对比：approvals.py 不在本 lane 文件
           白名单内，桥里没有可挂的 Condition；从 api.py 外包裹桥方法（含私有
           _register）侵入性强且脆。挂起侧按 SSE_APPROVAL_POLL_SECONDS (0.1s)
           轮询 list_pending() 快照 diff——新卡 → approval_suspended 帧；已见卡
           从快照消失 → approval_resolved 帧。解决侧帧数据取自收口台账
           （_approval_resolutions：api.py 内全部收口点登记，登记点即 kitagent
           session.approve/_decide 里 projection.put 的同位位置；登记后
           _event_cond.notify 即时唤醒，解决帧延迟近似 0）；台账无记录的路径
           （F6 同步桥回调内的 300s 超时收口，approvals.py 闭包内发生）以
           record_status 兜底，decision/resolved_by 置空。0.1s 轮询对人类尺度的
           审批卡不可感知。帧只覆盖本线程 scope（scope_type=thread 且
           scope_id=本线程）——流是 per-thread 投影，任务卡走 /v1/approvals 与
           任务事件面。快照间隔内登记又收口的卡不产生帧（无人来得及看见），
           消费方须容忍 resolved 无前置 suspended。审批/resync 帧不写 id: 行
           （非 events 表行，不得推进客户端重放游标）。
        2. token 合帧（50ms）：live 段连续 message.delta 行并入缓冲，窗口到期
           （或体量超 SSE_DELTA_MAX_CHARS）合并为单帧推送，data.text 拼接，id:
           取段末行 id（重放游标不回退）；非 delta 行到达即先冲刷缓冲再直通，
           帧序与 id 单调性不变。回放段（客户端 since 游标的追赶首拍）逐行原文
           下发——A7 契约「SSE data 即落库 payload 原文」由 tests/test_event_seq
           锁定；合帧只发生在推送侧（kitagent flush 同为广播层合帧，存储不合并）。
           合帧帧不带行级 seq（seq 已随行折叠；id: 行仍承载位置）。
        3. 队列满 resync（单连接积压防护）：本实现无独立消费队列，积压表现为
           单次轮询拉到的行数膨胀（客户端消费慢 → 写阻塞 → 积压继续增长）。
           非 delta 行数超过 SSE_BACKLOG_HIGH_WATER 时不再整批下发：先冲刷
           delta 缓冲，发 event: resync（data.since=客户端最后收到的行 id，
           data.skipped_up_to=跳过段末行 id），内部游标跳到段末，流继续。
           取舍：跳过段不在本流重发，客户端须按 data.since 重拉快照（
           /v1/threads/{id}/events 全量按 id 去重，或重开流），重拉与续流可能
           重叠 → 按 id 单调去重；delta 行不计入高水位——它们由机制 2 折叠，
           帧成本有界。kitagent 语义对应：订阅队列满 → 丢弃订阅者 +
           {"type": "resync"}，客户端重拉快照。
        """

        last_written = last_id  # 客户端实际收到的最后一个行 id（resync data.since 基准）
        seen_approvals: set[str] = set()
        last_approval_poll = 0.0  # 首拍立即对比（monotonic 远大于 0）
        last_ping = time.monotonic()
        first_poll = True  # 首拍 = 回放段（逐行原文）；此后为 live 段（合帧）
        pending_deltas: list[tuple[int, str]] = []
        pending_chars = 0
        delta_deadline: float | None = None

        def flush_deltas() -> None:
            nonlocal pending_chars, delta_deadline, last_written
            if not pending_deltas:
                return
            text = "".join(item[1] for item in pending_deltas)
            merged_id = pending_deltas[-1][0]
            frame = _sse_frame(
                "message.delta", json.dumps({"text": text}, ensure_ascii=False), row_id=merged_id
            )
            request.wfile.write(frame.encode("utf-8"))
            request.wfile.flush()
            last_written = merged_id
            pending_deltas.clear()
            pending_chars = 0
            delta_deadline = None

        while not self._stop.is_set():
            wrote = False
            rows = self._events_since(thread_id, last_id)
            replay_batch = first_poll
            first_poll = False
            if rows:
                last_id = rows[-1][0]
                non_delta = sum(
                    1 for _row_id, event_type, _row_payload in rows if event_type != "message.delta"
                )
                if non_delta > SSE_BACKLOG_HIGH_WATER:
                    # 机制 3：积压整段跳过 + resync 语义帧（先冲刷 delta 缓冲，
                    # 让 data.since 精确指向客户端真正收到的位置）。
                    flush_deltas()
                    data = json.dumps(
                        {"reason": "backlog", "since": last_written, "skipped_up_to": last_id},
                        ensure_ascii=False,
                    )
                    request.wfile.write(_sse_frame("resync", data).encode("utf-8"))
                    request.wfile.flush()
                    wrote = True
                elif replay_batch:
                    # 机制 2 的回放例外：追赶段逐行原文（A7 契约）
                    body = "".join(
                        _sse_frame(event_type, row_payload, row_id=row_id)
                        for row_id, event_type, row_payload in rows
                    )
                    request.wfile.write(body.encode("utf-8"))
                    request.wfile.flush()
                    last_written = last_id
                    wrote = True
                else:
                    # 机制 2：live 段合帧
                    for row_id, event_type, row_payload in rows:
                        if event_type == "message.delta":
                            text = _delta_text(row_payload)
                            if text is None:
                                # 防御：无 text 的 delta 行按原文即时直通
                                flush_deltas()
                                request.wfile.write(
                                    _sse_frame(event_type, row_payload, row_id=row_id).encode(
                                        "utf-8"
                                    )
                                )
                                request.wfile.flush()
                                last_written = row_id
                                wrote = True
                            else:
                                pending_deltas.append((row_id, text))
                                pending_chars += len(text)
                                if delta_deadline is None:
                                    delta_deadline = time.monotonic() + SSE_DELTA_FLUSH_SECONDS
                                if pending_chars >= SSE_DELTA_MAX_CHARS:
                                    flush_deltas()
                                    wrote = True
                        else:
                            flush_deltas()
                            request.wfile.write(
                                _sse_frame(event_type, row_payload, row_id=row_id).encode("utf-8")
                            )
                            request.wfile.flush()
                            last_written = row_id
                            wrote = True
            # 机制 1：审批登记表快照对比（间隔闸门，避免事件紧循环反复取锁）
            now = time.monotonic()
            if now - last_approval_poll >= SSE_APPROVAL_POLL_SECONDS:
                last_approval_poll = now
                if self._emit_approval_frames(request, thread_id, seen_approvals):
                    wrote = True
            # 到期合帧冲刷：批尾 delta 最多滞留一个窗口（50ms）
            if pending_deltas and delta_deadline is not None and time.monotonic() >= delta_deadline:
                flush_deltas()
                wrote = True
            if wrote:
                last_ping = time.monotonic()
                continue
            # 等待：事件唤醒 / 合帧期限 / 审批轮询闸门 / 心跳兜底
            timeout = SSE_HEARTBEAT_SECONDS
            if pending_deltas and delta_deadline is not None:
                timeout = min(timeout, max(0.0, delta_deadline - time.monotonic()))
            timeout = min(timeout, SSE_APPROVAL_POLL_SECONDS)
            with self._event_cond:
                self._event_cond.wait(timeout)
            if time.monotonic() - last_ping >= SSE_HEARTBEAT_SECONDS:
                request.wfile.write(b": ping\n\n")
                request.wfile.flush()
                last_ping = time.monotonic()

    def _emit_approval_frames(
        self, request: BaseHTTPRequestHandler, thread_id: str, seen: set[str]
    ) -> bool:
        """A10 机制 1 的单次快照对比：新挂起卡 → approval_suspended 帧；已见卡
        从快照消失 → approval_resolved 帧（数据优先取收口台账，无台账的收口
        路径以 record_status 兜底）。返回是否写出过帧。"""

        cards = [
            card
            for card in self.approval_bridge.list_pending()
            if card["scope_type"] == "thread" and card["scope_id"] == thread_id
        ]
        wrote = False
        for card in cards:
            if card["id"] in seen:
                continue
            seen.add(card["id"])
            data = json.dumps(
                {
                    "approval_id": card["id"],
                    "tool_name": card["tool_name"],
                    "description": card["description"],
                    "scope_type": card["scope_type"],
                    "scope_id": card["scope_id"],
                    "reason": "approval_pending",
                },
                ensure_ascii=False,
            )
            request.wfile.write(_sse_frame("approval_suspended", data).encode("utf-8"))
            request.wfile.flush()
            wrote = True
        current = {card["id"] for card in cards}
        for approval_id in seen - current:
            seen.discard(approval_id)
            entry = self._approval_resolution_for(approval_id)
            if entry is not None:
                payload = {
                    "id": approval_id,
                    "approval_id": approval_id,
                    "decision": entry["decision"],
                    "resolved_by": entry["resolved_by"],
                    "status": entry["status"],
                }
            else:
                # F6 同步桥回调内的超时收口（approvals.py 闭包内发生，无台账）：
                # record_status 只给状态位，decision/resolved_by 不可得 → 置空。
                payload = {
                    "id": approval_id,
                    "approval_id": approval_id,
                    "decision": None,
                    "resolved_by": None,
                    "status": self.approval_bridge.record_status(approval_id),
                }
            frame = _sse_frame("approval_resolved", json.dumps(payload, ensure_ascii=False))
            request.wfile.write(frame.encode("utf-8"))
            request.wfile.flush()
            wrote = True
        return wrote

    def _record_approval_resolution(
        self,
        approval_id: str,
        decision: str | None,
        resolved_by: str | None,
        status: str,
    ) -> None:
        """A10：审批收口入台账并唤醒 SSE 等待者（数据源契约见 __init__ 注释）。"""

        with self._approval_resolutions_guard:
            self._approval_resolutions.append(
                {
                    "id": approval_id,
                    "decision": decision,
                    "resolved_by": resolved_by,
                    "status": status,
                }
            )
        with self._event_cond:
            self._event_cond.notify_all()

    def _approval_resolution_for(self, approval_id: str) -> dict[str, Any] | None:
        with self._approval_resolutions_guard:
            for entry in reversed(self._approval_resolutions):
                if entry["id"] == approval_id:
                    return dict(entry)
        return None

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


def _sse_frame(event_type: str, data: str, *, row_id: int | None = None) -> str:
    """A10：组装一帧 SSE 文本（既有格式 id:/event:/data: 不变）。

    row_id 为 None 的帧（审批/resync）不写 id: 行——它们不是 events 表行，
    不得推进客户端的重放游标。
    """

    prefix = f"id: {row_id}\n" if row_id is not None else ""
    return f"{prefix}event: {event_type}\ndata: {data}\n\n"


def _delta_text(payload: str) -> str | None:
    """A10：提取 message.delta 行的 text（合帧拼接用）。

    不可解析或缺 text 字段返回 None——调用方对该行走原文即时直通，不做合帧。
    """

    try:
        value = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        return value["text"]
    return None


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
