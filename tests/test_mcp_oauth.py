"""Tests for MCP OAuth 2.0 (PKCE) primitives and the 401-refresh client wiring.

Zero real network: the default token poster (``nexuscli.mcp.auth._post_json``)
and the streamable-http transport/session are replaced with in-process fakes.
Every token value below is an obviously fake placeholder produced by
``_fake_access_token``/``_fake_refresh_token`` (assembled from string parts so
no usable credential literal ever appears in source), never a real credential.
HOME/USERPROFILE are isolated so the token store never touches the real
``~/.nexuscli``.
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any

import pytest

from nexuscli.mcp import auth as mcp_auth
from nexuscli.mcp.auth import (
    OAuthTokens,
    TokenStore,
    build_authorize_url,
    exchange_code,
    generate_pkce_verifier,
    pkce_challenge,
    refresh_tokens,
)
from nexuscli.mcp.client import McpClientManager
from nexuscli.mcp.config import McpAuthConfig, McpServerSpec, load_mcp_server_specs


def _fake_access_token(mark: str = "") -> str:
    """Obviously fake access-token placeholder (concatenated, never real)."""
    value = "test-access" + "-token"
    return f"{value}-{mark}" if mark else value


def _fake_refresh_token(mark: str = "") -> str:
    """Obviously fake refresh-token placeholder (concatenated, never real)."""
    value = "test-refresh" + "-token"
    return f"{value}-{mark}" if mark else value


def _install_home(tmp_path, monkeypatch) -> None:
    """Isolate Path.home() into tmp_path (win32 prefers USERPROFILE)."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


def _fake_token_response() -> dict[str, Any]:
    return {
        "access_token": _fake_access_token("2"),
        "refresh_token": _fake_refresh_token("2"),
        "expires_in": 3600,
    }


def _recording_post(recorder: list[dict[str, Any]]):
    async def fake_post(url: str, data: dict[str, Any]) -> dict[str, Any]:
        recorder.append({"url": url, "data": data})
        return _fake_token_response()

    return fake_post


def _install_http_fakes(monkeypatch):
    """Stub the streamable-http transport and session inside nexuscli.mcp.client.

    The fake transport records the headers kwarg of every call; the fake
    session raises a 401-shaped RuntimeError on the first initialize() only.
    """
    captured_headers: list[dict[str, str] | None] = []
    state = {"initialize_calls": 0}

    class _FakeHttpTransport:
        async def __aenter__(self):
            return (object(), object(), "fake-session-id")

        async def __aexit__(self, *exc_info):
            return False

    class _FakeClientSession:
        def __init__(self, read, write):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def initialize(self):
            state["initialize_calls"] += 1
            if state["initialize_calls"] == 1:
                raise RuntimeError("Server returned HTTP 401 Unauthorized")

        async def list_tools(self):
            return SimpleNamespace(tools=[])

    def fake_streamablehttp_client(url, headers=None, timeout=None):
        captured_headers.append(headers)
        return _FakeHttpTransport()

    monkeypatch.setattr("nexuscli.mcp.client.streamablehttp_client", fake_streamablehttp_client)
    monkeypatch.setattr("nexuscli.mcp.client.ClientSession", _FakeClientSession)
    return captured_headers, state


def test_pkce_challenge_matches_rfc7636_vector():
    # RFC 7636 Appendix B official test vector.
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert pkce_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"

    generated = generate_pkce_verifier()
    assert len(generated) == 64
    assert set(generated) <= set(mcp_auth._PKCE_CHARS)

    first = pkce_challenge(generate_pkce_verifier())
    second = pkce_challenge(generate_pkce_verifier())
    assert first != second


def test_authorize_url_carries_s256_params():
    cfg = McpAuthConfig(
        client_id="nexuscli",
        authorize_url="https://mcp.example/authorize",
        token_url="https://mcp.example/token",
        scopes=["read"],
    )
    url = build_authorize_url(cfg, "challenge-value", "state-value")
    assert url.startswith("https://mcp.example/authorize?")
    assert "response_type=code" in url
    assert "client_id=nexuscli" in url
    assert "code_challenge=challenge-value" in url
    assert "code_challenge_method=S256" in url
    assert "state=state-value" in url
    assert "scope=read" in url

    no_scope = build_authorize_url(McpAuthConfig(client_id="nexuscli"), "c", "s")
    assert "scope" not in no_scope


def test_exchange_and_refresh_payloads():
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_post(url: str, data: dict[str, Any]) -> dict[str, Any]:
        calls.append((url, data))
        return {
            "access_token": _fake_access_token(),
            "refresh_token": _fake_refresh_token(),
            "expires_in": 3600,
        }

    cfg = McpAuthConfig(client_id="nexuscli", token_url="https://mcp.example/token")
    tokens = asyncio.run(exchange_code(cfg, "auth-code", "verifier-value", post_json=fake_post))
    url, data = calls[-1]
    assert url == "https://mcp.example/token"
    assert data["grant_type"] == "authorization_code"
    assert data["code"] == "auth-code"
    assert data["code_verifier"] == "verifier-value"
    assert data["client_id"] == "nexuscli"
    assert data["redirect_uri"] == cfg.redirect_uri
    assert tokens.access_token == _fake_access_token()
    assert tokens.refresh_token == _fake_refresh_token()
    assert tokens.expires_at == pytest.approx(time.time() + 3600, abs=5)

    refreshed = asyncio.run(refresh_tokens(cfg, _fake_refresh_token("old"), post_json=fake_post))
    _, data = calls[-1]
    assert data["grant_type"] == "refresh_token"
    assert data["refresh_token"] == _fake_refresh_token("old")
    assert data["client_id"] == "nexuscli"
    assert "code_verifier" not in data
    assert refreshed.access_token == _fake_access_token()
    assert refreshed.expires_at == pytest.approx(time.time() + 3600, abs=5)

    # A server that does not rotate refresh tokens: the old value is kept.
    async def fake_post_no_rotate(url: str, data: dict[str, Any]) -> dict[str, Any]:
        return {"access_token": _fake_access_token("3"), "expires_in": 60}

    kept = asyncio.run(
        refresh_tokens(cfg, _fake_refresh_token("kept"), post_json=fake_post_no_rotate)
    )
    assert kept.refresh_token == _fake_refresh_token("kept")


def test_token_store_writes_under_nexuscli_with_placeholder_values(tmp_path, monkeypatch):
    _install_home(tmp_path, monkeypatch)
    store = TokenStore()
    store.save(
        "graph",
        OAuthTokens(
            access_token=_fake_access_token(),
            refresh_token=_fake_refresh_token(),
            expires_at=123.0,
        ),
    )
    path = tmp_path / "home" / ".nexuscli" / "mcp-oauth.json"
    assert path.exists()
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["servers"]["graph"]["access_token"] == _fake_access_token()
    assert document["servers"]["graph"]["refresh_token"] == _fake_refresh_token()

    loaded = store.tokens_for("graph")
    assert loaded is not None
    assert loaded.refresh_token == _fake_refresh_token()
    assert loaded.expires_at == 123.0

    path.write_text("{not json", encoding="utf-8")  # corrupt file -> empty store
    assert store.load() == {"servers": {}}
    assert store.tokens_for("graph") is None


def test_default_post_json_rejects_private_and_local_urls():
    for bad in [
        "http://localhost/token",
        "http://127.0.0.1/token",
        "http://192.168.1.5/token",
        "file:///tmp/x",
    ]:
        with pytest.raises(ValueError):
            mcp_auth._validate_token_url(bad)
    mcp_auth._validate_token_url("https://mcp.example/token")  # must not raise


def test_ensure_access_token_refresh_path(tmp_path, monkeypatch):
    _install_home(tmp_path, monkeypatch)
    store = TokenStore()
    store.save(
        "graph",
        OAuthTokens(
            access_token=_fake_access_token("stale"),
            refresh_token=_fake_refresh_token(),
            expires_at=time.time() - 10,
        ),
    )
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr("nexuscli.mcp.auth._post_json", _recording_post(calls))

    spec = McpServerSpec(
        name="graph",
        type="http",
        url="https://mcp.example/mcp",
        auth=McpAuthConfig(client_id="nexuscli", token_url="https://mcp.example/token"),
    )
    token = asyncio.run(mcp_auth.ensure_access_token(spec))
    assert token == _fake_access_token("2")
    assert calls[0]["data"]["grant_type"] == "refresh_token"
    updated = store.tokens_for("graph")
    assert updated is not None
    assert updated.access_token == _fake_access_token("2")

    # A spec without an auth section yields None (and posts nothing).
    plain = McpServerSpec(name="plain", type="http", url="https://mcp.example/mcp")
    assert asyncio.run(mcp_auth.ensure_access_token(plain)) is None


def test_http_transport_attaches_header_and_retries_once_on_401(tmp_path, monkeypatch):
    _install_home(tmp_path, monkeypatch)
    (tmp_path / ".nexuscli").mkdir()
    (tmp_path / ".nexuscli" / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "graph": {
                        "type": "http",
                        "url": "https://mcp.example/mcp",
                        "auth": {
                            "client_id": "nexuscli",
                            "authorize_url": "https://mcp.example/authorize",
                            "token_url": "https://mcp.example/token",
                            "scopes": ["read"],
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    TokenStore().save(
        "graph",
        OAuthTokens(
            access_token=_fake_access_token(),
            refresh_token=_fake_refresh_token(),
            expires_at=time.time() + 3600,
        ),
    )

    captured_headers, _state = _install_http_fakes(monkeypatch)
    post_calls: list[dict[str, Any]] = []
    monkeypatch.setattr("nexuscli.mcp.auth._post_json", _recording_post(post_calls))

    manager = McpClientManager(tmp_path)
    tools = asyncio.run(manager.list_server_tools(manager.specs["graph"]))
    assert tools == []

    assert len(captured_headers) == 2
    assert captured_headers[0] is not None
    assert captured_headers[0]["Authorization"] == f"Bearer {_fake_access_token()}"
    assert captured_headers[1] is not None
    assert captured_headers[1]["Authorization"] == f"Bearer {_fake_access_token('2')}"
    assert len(post_calls) == 1
    assert post_calls[0]["data"]["refresh_token"] == _fake_refresh_token()


def test_401_without_refresh_token_propagates(tmp_path, monkeypatch):
    _install_home(tmp_path, monkeypatch)
    TokenStore().save(
        "graph",
        OAuthTokens(access_token=_fake_access_token(), expires_at=time.time() + 3600),
    )

    captured_headers, state = _install_http_fakes(monkeypatch)
    post_calls: list[dict[str, Any]] = []
    monkeypatch.setattr("nexuscli.mcp.auth._post_json", _recording_post(post_calls))

    spec = McpServerSpec(
        name="graph",
        type="http",
        url="https://mcp.example/mcp",
        auth=McpAuthConfig(client_id="nexuscli", token_url="https://mcp.example/token"),
    )
    with pytest.raises(RuntimeError, match="401"):
        asyncio.run(McpClientManager(tmp_path).list_server_tools(spec))

    assert post_calls == []  # nothing to refresh with: no token request at all
    assert len(captured_headers) == 1  # exactly one attempt, no retry
    assert state["initialize_calls"] == 1


def test_spec_auth_parsing_and_unknown_type_ignored(tmp_path, monkeypatch):
    _install_home(tmp_path, monkeypatch)
    monkeypatch.setenv("MCP_OAUTH_TEST_CLIENT_ID", "nexuscli")
    (tmp_path / ".nexuscli").mkdir()
    (tmp_path / ".nexuscli" / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "graph": {
                        "type": "http",
                        "url": "https://mcp.example/mcp",
                        "auth": {
                            "client_id": "${MCP_OAUTH_TEST_CLIENT_ID}",
                            "token_url": "https://mcp.example/token",
                            "scopes": ["read"],
                        },
                    },
                    "legacy": {
                        "type": "http",
                        "url": "https://mcp.example/mcp",
                        "auth": {"type": "mtls", "client_id": "other"},
                    },
                    "noauth": {"type": "http", "url": "https://mcp.example/mcp"},
                }
            }
        ),
        encoding="utf-8",
    )
    specs = load_mcp_server_specs(tmp_path)
    assert specs["graph"].auth is not None
    assert specs["graph"].auth.client_id == "nexuscli"  # ${VAR} expanded
    assert specs["graph"].auth.redirect_uri == "http://localhost:8765/callback"
    assert specs["graph"].auth.scopes == ["read"]
    assert specs["legacy"].auth is None  # unknown auth type safely ignored
    assert specs["noauth"].auth is None
