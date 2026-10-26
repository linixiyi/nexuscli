"""工作台会话鉴权（F14）：一次性启动口令 + HttpOnly Cookie 会话 + CSRF。

模型对标 kitagent web/gateway.py（/tmp/kitagent-src 实读定稿，用户直接指令「按照他的来」）：

- 服务启动生成三个秘密：bootstrap_token（一次性启动口令）、cookie（会话凭证）、csrf；
  终端打印 ``http://127.0.0.1:<port>/#bootstrap=<token>`` 一键链接。
- 浏览器从 URL 片段读出口令，POST /v1/workbench/bootstrap 换发——**一次性**（bootstrapped
  标志，重放/错口令一律 403），成功后 Set-Cookie（HttpOnly + SameSite=strict，JS 不可读）
  并在 JSON 里返回 csrf。
- 之后浏览器请求凭 cookie（同源自动携带）；变更请求（POST/PATCH）另须 x-csrf-token 头
  （compare_digest 比对）——cookie 自动携带挡不住跨源伪造，csrf 补上这一刀。
- 静态资源免认证；外部自动化通道（x-api-key/Bearer 头、SSE 的 api_key 查询参数）原样保留。

会话生命周期 = 进程生命周期：不持久化、不刷新、重启即新一轮口令（kitagent 同款语义）。

A6 会话权限模式（Ported from kitagent (MIT, zed123214/kitagent)
core/permissions/policy.py:13-18 SessionPermissions + manager.py:66-108
configure_session 的 revision 乐观锁）：SessionPermissions{mode, revoked_tools,
revision} 仅内存——进程生命周期即权限生命周期（kitagent「never persisted as
global policy」同款；manager.py:389-404 的断连清空在单用户 CLI 无断连面，等价
语义 = 随进程消亡）。
"""

from __future__ import annotations

import secrets
import threading
from typing import Any, Literal

from nexuscli.tools.base import ToolDecision

COOKIE_NAME = "nexuscli_workbench"
_CSRF_HEADER = "x-csrf-token"

PermissionMode = Literal["read_only", "ask", "auto"]
PERMISSION_MODES: tuple[PermissionMode, ...] = ("read_only", "ask", "auto")


class RevisionConflict(Exception):
    """A6 乐观锁冲突（kitagent manager.py:77-78 同款）：expected_revision 与当前
    revision 不匹配。端点层映射 409，客户端刷新状态后携带新 revision 重试。"""

    def __init__(self, expected_revision: int, current_revision: int) -> None:
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__(
            f"session permissions changed (expected revision {expected_revision}, "
            f"current {current_revision}); refresh and retry"
        )


class SessionPermissions:
    """会话权限模式与单项撤销（仅内存，单会话单实例）。

    与 kitagent 的差异（有意）：kitagent 用 allowed_tools 记「本会话已 allow 的
    工具」，本仓把会话面建模为 revoked_tools（单项撤销集）——长期信任的 allow
    规则在配置/门链层（AGENTS.md allow 语义），会话面只做收紧，不新增放行。
    """

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self.mode: PermissionMode = "ask"
        self.revoked_tools: set[str] = set()
        self.revision: int = 0

    def static_gate(self, tool_name: str) -> ToolDecision | None:
        """会话模式静态预判（回调预判与 recheck_pending 共用同一把尺）。

        返回 None = 交回既有门链/人审（kitagent manager.py:151-168 的 session
        权限层同位）。revoked 先于模式：auto 也不能放行被单项撤销的工具。
        """

        with self._guard:
            if tool_name in self.revoked_tools:
                return "deny"
            if self.mode == "read_only":
                return "deny"
            if self.mode == "auto":
                return "approve"
            return None

    def configure(
        self,
        *,
        expected_revision: int,
        mode: PermissionMode | None = None,
        revoke_tool: str | None = None,
    ) -> dict[str, Any]:
        """修订校验的权限变更（kitagent manager.configure_session:66-97 同款乐观锁）。

        - expected_revision 不匹配当前 → RevisionConflict（端点层 409）；
        - mode 切到 read_only/auto → 清空 revoked_tools（「清空授权」：换模式即
          全新起点，kitagent manager.py:79-83 对 grants 的清空同取向）；
        - revoke_tool 单项撤销（manager.py:86 的单项过滤在此以撤销集建模）。

        成功返回变更后的快照（revision +1）；快照是普通 dict，不共享锁。
        """

        if mode is not None and mode not in PERMISSION_MODES:
            raise ValueError(f"mode must be one of: {', '.join(PERMISSION_MODES)}")
        if revoke_tool is not None and not revoke_tool:
            raise ValueError("revoke_tool must be a non-empty tool name")
        with self._guard:
            if expected_revision != self.revision:
                raise RevisionConflict(expected_revision, self.revision)
            if mode in {"read_only", "auto"}:
                self.revoked_tools.clear()
            if mode is not None:
                self.mode = mode
            if revoke_tool is not None:
                self.revoked_tools.add(revoke_tool)
            self.revision += 1
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict[str, Any]:
        """快照（调用方持有 _guard——Lock 不可重入，勿在持锁路径调 snapshot()）。"""

        return {
            "mode": self.mode,
            "revoked_tools": sorted(self.revoked_tools),
            "revision": self.revision,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._guard:
            return self._snapshot_locked()


class WebSession:
    """单会话（进程生命周期即会话生命周期）的三秘密与授权判定。"""

    def __init__(self) -> None:
        self.bootstrap_token = secrets.token_urlsafe(32)
        self.cookie = secrets.token_urlsafe(32)
        self.csrf = secrets.token_urlsafe(32)
        self.bootstrapped = False

    def bootstrap(self, presented: str | None) -> str | None:
        """一次性换发：口令正确且未使用 → 返回 csrf 并置位；否则 None（端点层映射 403）。"""

        if self.bootstrapped or presented is None or not presented:
            return None
        if not secrets.compare_digest(presented, self.bootstrap_token):
            return None
        self.bootstrapped = True
        return self.csrf

    def authorize(self, cookie_value: str | None) -> bool:
        """浏览器会话门：cookie 比对（未 bootstrap 前恒 False）。"""

        return (
            self.bootstrapped
            and cookie_value is not None
            and bool(cookie_value)
            and secrets.compare_digest(cookie_value, self.cookie)
        )

    def csrf_matches(self, presented: str | None) -> bool:
        return (
            self.bootstrapped
            and presented is not None
            and bool(presented)
            and secrets.compare_digest(presented, self.csrf)
        )

    def reissue(self, cookie_value: str | None) -> str | None:
        """U2 csrf 重签 + 多标签页收敛：持有效会话 cookie → 返回当前 csrf（不轮换）。

        csrf 由「bootstrap 一次下发」放宽为「会话级衍生秘密」（spec §2.2，kitagent
        同款语义）：浏览器刷新丢掉内存副本后，凭仍有效的会话 cookie 可取回当前值
        恢复写能力（csrf 防的是跨源伪造，持 cookie 的同源客户端本就能发请求，取回
        不降低防线）。cookie 比对与 authorize 同一 compare_digest 口径。

        不轮换是 2026-10-27 复审修复（多标签页单值轮换回归）：早期实现「重签即
        生成新值替换」，第二标签页冷加载自动重签会把已打开旧标签页持有的 csrf
        踢成 403，旧标签页无页内恢复路径只能刷新——刷新又轮掉对方，乒乓。收敛
        语义下 csrf 进程内恒定，任意多标签页重签取回同一值；单值语义不变（任何
        时刻服务端恰有一个 csrf，spec §5 不引入多 csrf 值），§2.4「不受多标签页
        影响」从只对重签方成立变为对全部标签页成立。不匹配返回 None（端点层映射
        401）。
        """

        if not self.bootstrapped or not cookie_value:
            return None
        if not secrets.compare_digest(cookie_value, self.cookie):
            return None
        return self.csrf


def session_cookie_header(value: str) -> str:
    """Set-Cookie 值：HttpOnly（JS 不可读）+ SameSite=strict（跨站不携带）+ path=/。"""

    return f"{COOKIE_NAME}={value}; HttpOnly; SameSite=strict; Path=/"


def cookie_value_from_header(cookie_header: str | None) -> str | None:
    """从请求的 Cookie 头提取会话值；无/畸形返回 None。"""

    if not cookie_header:
        return None
    for pair in cookie_header.split(";"):
        name, _, value = pair.strip().partition("=")
        if name == COOKIE_NAME and value:
            return value
    return None


def csrf_header_name() -> str:
    return _CSRF_HEADER
