"""Loopback origin guard shared by the MCP server and the Runtime API.

浏览器发起的跨源请求必须带 Origin；非浏览器客户端（curl、SDK）通常不带。
只放行本机来源，其余一律拒绝——否则本机任意网页都能 POST 到 127.0.0.1 跑工具。
"""

from __future__ import annotations

from urllib.parse import urlsplit

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def is_loopback_origin(origin: str) -> bool:
    try:
        hostname = urlsplit(origin).hostname
    except ValueError:
        return False
    # urlsplit strips the IPv6 brackets, so the set holds the bare "::1".
    return hostname is not None and hostname.lower() in LOOPBACK_HOSTS
