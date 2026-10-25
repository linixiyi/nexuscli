"""工作台会话鉴权（F14）：一次性启动口令 + HttpOnly Cookie 会话 + CSRF。

模型对标 kitagent web/gateway.py（/tmp/kitagent-src 实读定稿，用户直接指令「按照他的来」）：

- 服务启动生成三个秘密：bootstrap_token（一次性启动口令）、cookie（会话凭证）、csrf；
  终端打印 ``http://127.0.0.1:<port>/#bootstrap=<token>`` 一键链接。
- 浏览器从 URL 片段读出口令，POST /v1/workbench/bootstrap 换发——**一次性**（bootstrapped
  标志，重放/错口令一律 403），成功后 Set-Cookie（HttpOnly + SameSite=strict，JS 不可读）
  并在 JSON 里返回 csrf。
- 之后浏览器请求凭 cookie（同源自动携带）；变更请求（POST）另须 x-csrf-token 头
  （compare_digest 比对）——cookie 自动携带挡不住跨源伪造，csrf 补上这一刀。
- 静态资源免认证；外部自动化通道（x-api-key/Bearer 头、SSE 的 api_key 查询参数）原样保留。

会话生命周期 = 进程生命周期：不持久化、不刷新、重启即新一轮口令（kitagent 同款语义）。
"""

from __future__ import annotations

import secrets

COOKIE_NAME = "nexuscli_workbench"
_CSRF_HEADER = "x-csrf-token"


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
