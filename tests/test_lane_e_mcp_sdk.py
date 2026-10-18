"""Lane E — MCP filtering over a real stdio server, OAuth token storage, SDK engine.

Surfaces covered (docs/testing-matrix.md rows assigned to this lane):
- 10.2 MCP 工具过滤: allowed/blocked filters against a *real* stdio server process
- 10.3 OAuth 令牌落盘: token file permission 0600 + flow against a local fake server
- 11.9 SDK: create_default_engine builds a real engine (offline assertions; the
  gateway turn itself is exercised outside the suite)
"""

from __future__ import annotations

import asyncio
import http.server
import json
import sys
import threading
from pathlib import Path

import httpx
import pytest

from nexuscli.mcp import McpClientManager
from nexuscli.mcp import auth as mcp_auth
from nexuscli.mcp.config import McpAuthConfig, McpServerSpec
from nexuscli.sdk import QueryEngine, create_default_engine

# ---------------------------------------------------------------------------
# MCP: real stdio server + config-file filters (10.2)
# ---------------------------------------------------------------------------

_MCP_SERVER_SCRIPT = """\
import sys
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("lane-demo", host="127.0.0.1")

@mcp.tool()
def echo(text: str) -> str:
    \"\"\"Echo the text back.\"\"\"
    return f"echo: {text}"

@mcp.tool()
def ping() -> str:
    \"\"\"Liveness probe.\"\"\"
    return "pong"

@mcp.tool()
def secret() -> str:
    \"\"\"A tool the filter must keep out.\"\"\"
    return "s3cret"

mcp.run(transport="stdio")
"""


@pytest.fixture()
def filtered_project(tmp_path: Path) -> Path:
    server = tmp_path / "demo_mcp_server.py"
    server.write_text(_MCP_SERVER_SCRIPT, encoding="utf-8")
    config_dir = tmp_path / ".nexuscli"
    config_dir.mkdir()
    command = json.dumps(sys.executable)
    (config_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "allowonly": {
                        "type": "stdio",
                        "command": sys.executable,
                        "args": [str(server)],
                        "allowed_tools": ["echo"],
                    },
                    "blockone": {
                        "type": "stdio",
                        "command": sys.executable,
                        "args": [str(server)],
                        "blocked_tools": ["secret"],
                    },
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _ = command
    return tmp_path


def _tool_names(manager: McpClientManager) -> set[str]:
    return {tool.name for tool in asyncio.run(manager.load_tools())}


def test_allowed_tools_whitelist_visible_over_real_stdio(filtered_project: Path) -> None:
    manager = McpClientManager(str(filtered_project))
    try:
        names = _tool_names(manager)
    finally:
        asyncio.run(manager.aclose())

    # allowonly: only `echo` passes the whitelist (+ the two resource views).
    assert "mcp__allowonly__echo" in names, sorted(names)
    assert "mcp__allowonly__ping" not in names, sorted(names)
    assert "mcp__allowonly__secret" not in names, sorted(names)
    # blockone: everything except `secret` stays visible.
    assert "mcp__blockone__echo" in names and "mcp__blockone__ping" in names
    assert "mcp__blockone__secret" not in names, sorted(names)


def test_whitelisted_tool_round_trips_over_real_stdio(filtered_project: Path) -> None:
    manager = McpClientManager(str(filtered_project))
    try:
        tools = {tool.name: tool for tool in asyncio.run(manager.load_tools())}
        tool = tools["mcp__allowonly__echo"]

        async def run() -> str:
            from nexuscli.config import load_config
            from nexuscli.tools.base import ToolContext

            context = ToolContext(
                cwd=str(filtered_project), config=load_config(project_root=str(filtered_project))
            )
            return (await tool.execute({"text": "lane-e"}, context)).content

        assert asyncio.run(run()) == "echo: lane-e"
    finally:
        asyncio.run(manager.aclose())


# ---------------------------------------------------------------------------
# OAuth (10.3)
# ---------------------------------------------------------------------------


def test_token_file_written_with_0600_permissions(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    store = mcp_auth.TokenStore()  # real default path: $HOME/.nexuscli/mcp-oauth.json

    store.save("graph", mcp_auth.OAuthTokens(access_token="at", refresh_token="rt"))

    path = tmp_path / ".nexuscli" / "mcp-oauth.json"
    assert path.exists()
    assert path.stat().st_mode & 0o777 == 0o600, oct(path.stat().st_mode)


class _FakeOAuthServer(http.server.BaseHTTPRequestHandler):
    """Minimal real token endpoint: validates the PKCE legs it receives."""

    seen: list[dict] = []
    issue_count = 0

    def do_POST(self):  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        fields = dict(fragment.split("=", 1) for fragment in body.split("&") if "=" in fragment)
        type(self).seen.append(fields)
        type(self).issue_count += 1
        payload = {
            "access_token": f"access-{type(self).issue_count}",
            "refresh_token": f"refresh-{type(self).issue_count}",
            "expires_in": 3600,
            "token_type": "bearer",
        }
        if fields.get("grant_type") == "refresh_token":
            payload["refresh_token"] = fields.get("refresh_token", "")  # rotation-optional
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):  # noqa: N802 (http.server API) — liveness probe target
        encoded = b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args) -> None:  # keep test output clean
        pass


@pytest.fixture()
def fake_oauth_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _FakeOAuthServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _FakeOAuthServer.seen = []
    _FakeOAuthServer.issue_count = 0
    yield f"http://localtest.me:{server.server_address[1]}/token"
    server.shutdown()
    server.server_close()


async def _run_oauth_flow(token_url: str, tmp_path) -> dict:
    cfg = McpAuthConfig(
        client_id="lane-e-test",
        authorize_url=f"{token_url.rsplit('/token', 1)[0]}/authorize",
        token_url=token_url,
        scopes=["mcp:read"],
    )
    verifier = mcp_auth.generate_pkce_verifier()
    tokens = await mcp_auth.exchange_code(cfg, "lane-e-auth-code", verifier)
    store = mcp_auth.TokenStore()
    store.save("lane-fake", tokens)
    spec = McpServerSpec(name="lane-fake", type="http", url="http://localtest.me/mcp", auth=cfg)
    cached = await mcp_auth.ensure_access_token(spec)

    # Age the stored token past the 30s clock-skew guard: the next
    # ensure_access_token must refresh over real HTTP and serve the new token.
    stale = mcp_auth.OAuthTokens(
        access_token="access-1", refresh_token="refresh-1", expires_at=0.0
    )
    store.save("lane-fake", stale)
    refreshed = await mcp_auth.ensure_access_token(spec)
    return {
        "tokens": tokens,
        "cached": cached,
        "refreshed": refreshed,
        "store": store,
        "path": store.path,
        "seen": list(_FakeOAuthServer.seen),
    }


def test_oauth_flow_against_local_fake_server(fake_oauth_server, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    result = asyncio.run(_run_oauth_flow(fake_oauth_server, tmp_path))

    # 1) PKCE exchange went over real HTTP to the local fake server.
    assert result["tokens"].access_token == "access-1", result["tokens"]
    grants = [fields.get("grant_type") for fields in result["seen"]]
    assert grants[0] == "authorization_code", grants
    assert result["seen"][0]["code_verifier"], grants  # PKCE verifier was sent
    # 2) A fresh stored token is served from cache (no needless refresh).
    assert result["cached"] == "access-1", result["cached"]
    # 3) A stale stored token triggers the refresh grant over real HTTP.
    assert result["refreshed"] == "access-2", result["refreshed"]
    assert grants[1] == "refresh_token", grants
    # 4) The token file landed with 0600.
    assert result["path"].stat().st_mode & 0o777 == 0o600


def test_default_transport_refuses_loopback_token_endpoint(fake_oauth_server) -> None:
    """The SSRF guard must refuse a loopback URL even when a server answers there."""
    loopback_url = fake_oauth_server.replace("localtest.me", "127.0.0.1")
    cfg = McpAuthConfig(client_id="c", token_url=loopback_url)

    with pytest.raises(ValueError, match="local/reserved"):
        asyncio.run(mcp_auth.exchange_code(cfg, "code", "verifier" * 10))


async def _probe_httpx_reaches_fake_server(url: str) -> bool:
    try:
        response = await httpx.AsyncClient(timeout=5.0).get(url)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def test_httpx_reaches_local_fake_server_through_hostname(fake_oauth_server) -> None:
    """httpx (the transport _post_json uses) can actually reach the fake server.

    The SSRF guard only rejects loopback *literals*; a DNS name that lands on
    this machine (localtest.me) is the supported way a local fake server gets
    exercised over real HTTP. This pins that the route really carries traffic
    in this environment.
    """
    base = fake_oauth_server.rsplit("/token", 1)[0]
    assert asyncio.run(_probe_httpx_reaches_fake_server(f"{base}/probe"))


# ---------------------------------------------------------------------------
# SDK (11.9)
# ---------------------------------------------------------------------------


def test_create_default_engine_builds_usable_engine(tmp_path: Path) -> None:
    engine = create_default_engine(cwd=str(tmp_path))

    assert isinstance(engine, QueryEngine)
    names = engine.tool_registry.list_names()
    for expected in ("read_file", "edit_file", "todo_write", "directory_tree", "get_file_info"):
        assert expected in names, expected
    assert "NexusCLI" in engine.system_prompt
    assert engine.cwd == str(tmp_path.resolve())
    # With no user config the default profile is active (offline assertion; the
    # gateway turn is exercised in the lane's real-run evidence).
    assert engine.llm_client.model_name == "deepseek-v4-flash"
    assert engine.config.features.mcp is True
