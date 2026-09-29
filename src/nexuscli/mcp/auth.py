"""OAuth 2.0 authorization-code + PKCE primitives for MCP HTTP servers.

Deliberately minimal slice (contrasted with ZCode's interactive oauth modules,
which are not ported): only externally triggerable token primitives live here —
PKCE generation, authorize-URL assembly, code exchange, refresh, a small
on-disk token store, and client wiring helpers. The interactive authorization
flow itself (browser launch, localhost callback listener, RFC 8414 discovery,
dynamic client registration, cross-process refresh locks) is out of scope;
tests drive this module with injected codes and fake posters.

Security posture:
- Public client only — no ``client_secret`` exists anywhere in this module;
  secrets never go into mcp.json and real credentials only arrive via the
  environment.
- Tokens are persisted only under ``~/.nexuscli/`` with best-effort 0600
  permissions, and token response fields are never logged.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import secrets
import string
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from nexuscli.mcp.config import McpAuthConfig, McpServerSpec

TokenPoster = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

# RFC 7636 unreserved alphabet for code verifiers.
_PKCE_CHARS = string.ascii_letters + string.digits + "-._~"


def generate_pkce_verifier(length: int = 64) -> str:
    """Random RFC 7636 code verifier (43-128 chars over the unreserved set)."""
    length = max(43, min(128, length))
    return "".join(secrets.choice(_PKCE_CHARS) for _ in range(length))


def pkce_challenge(verifier: str) -> str:
    """S256 challenge: BASE64URL-ENCODE(SHA256(ASCII(verifier))), no padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def build_authorize_url(cfg: McpAuthConfig, challenge: str, state: str) -> str:
    """Assemble the authorization-endpoint URL; scope is space-joined or omitted."""
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": cfg.client_id,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "redirect_uri": cfg.redirect_uri,
    }
    if cfg.scopes:
        params["scope"] = " ".join(cfg.scopes)
    return f"{cfg.authorize_url}?{urlencode(params)}"


@dataclass(slots=True)
class OAuthTokens:
    access_token: str
    refresh_token: str = ""
    expires_at: float = 0.0  # epoch seconds; 0 means "no expiry recorded"


def _tokens_from_response(payload: dict[str, Any], fallback_refresh_token: str = "") -> OAuthTokens:
    expires_in = payload.get("expires_in")
    expires_at = time.time() + float(expires_in) if expires_in else 0.0
    # A missing refresh_token keeps the previous one (rotation-optional servers).
    return OAuthTokens(
        access_token=str(payload.get("access_token", "")),
        refresh_token=str(payload.get("refresh_token") or fallback_refresh_token),
        expires_at=expires_at,
    )


async def exchange_code(
    cfg: McpAuthConfig,
    code: str,
    verifier: str,
    *,
    post_json: TokenPoster | None = None,
) -> OAuthTokens:
    """Exchange an authorization code for tokens (PKCE leg)."""
    poster = post_json or _post_json
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": cfg.client_id,
        "code_verifier": verifier,
        "redirect_uri": cfg.redirect_uri,
    }
    return _tokens_from_response(await poster(cfg.token_url, data))


async def refresh_tokens(
    cfg: McpAuthConfig,
    refresh_token: str,
    *,
    post_json: TokenPoster | None = None,
) -> OAuthTokens:
    """Rotate tokens via the refresh grant; no code_verifier on this leg."""
    poster = post_json or _post_json
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": cfg.client_id,
    }
    return _tokens_from_response(
        await poster(cfg.token_url, data),
        fallback_refresh_token=refresh_token,
    )


def _validate_token_url(url: str) -> None:
    """SSRF guard for the token endpoint (mirrors web/fetch.py, local copy).

    Only http/https with a non-empty host is allowed; localhost names and
    loopback/private/link-local/multicast/reserved IP literals are rejected.
    DNS resolution is deliberately not attempted here — the check stays
    network-free for tests — so hostname-based SSRF beyond the literal-IP
    checks is out of this slice's scope.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"OAuth token endpoint must be http/https: {url}")
    host = parsed.hostname or ""
    if not host:
        raise ValueError(f"OAuth token endpoint must include a host: {url}")
    lowered = host.lower()
    if lowered == "localhost" or lowered.endswith(".localhost"):
        raise ValueError(f"OAuth token endpoint must not target localhost: {url}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return  # hostname, not an IP literal
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
        raise ValueError(f"OAuth token endpoint must not target a local/reserved address: {url}")


async def _post_json(url: str, data: dict[str, Any]) -> dict[str, Any]:
    """Default token-POST transport (module-level named so tests can patch it)."""
    _validate_token_url(url)
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, data=data)
    if not response.is_success:  # anything outside 2xx is a failure
        raise RuntimeError(f"OAuth token endpoint returned HTTP {response.status_code}")
    return response.json()  # token response fields are never logged


class TokenStore:
    """On-disk OAuth token cache under ``~/.nexuscli/mcp-oauth.json``."""

    def __init__(self, path: Path | None = None):
        self.path = path or Path.home() / ".nexuscli" / "mcp-oauth.json"

    def load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):  # missing/unreadable/corrupt -> empty
            return {"servers": {}}
        if not isinstance(raw, dict) or not isinstance(raw.get("servers"), dict):
            return {"servers": {}}
        return raw

    def tokens_for(self, server_name: str) -> OAuthTokens | None:
        entry = self.load()["servers"].get(server_name)
        if not isinstance(entry, dict) or not entry.get("access_token"):
            return None
        return OAuthTokens(
            access_token=str(entry.get("access_token", "")),
            refresh_token=str(entry.get("refresh_token", "")),
            expires_at=float(entry.get("expires_at", 0.0)),
        )

    def save(self, server_name: str, tokens: OAuthTokens) -> None:
        document = self.load()
        document["servers"][server_name] = {
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "expires_at": tokens.expires_at,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        # Best-effort 0600: on NTFS chmod only toggles the read-only bit, so on
        # Windows this is symbolic. The file holds secrets, and Windows relies
        # on the user-profile ACLs as the real protection.
        with suppress(OSError):
            os.chmod(self.path, 0o600)


async def ensure_access_token(spec: McpServerSpec) -> str | None:
    """Return a usable access token for ``spec``, refreshing when stale.

    Returns None when the spec has no auth section or the store holds nothing
    usable — the interactive authorization flow is not part of this slice, so
    there is no way to obtain a first token from here.
    """
    if spec.auth is None:
        return None
    store = TokenStore()
    tokens = store.tokens_for(spec.name)
    if tokens and tokens.expires_at > time.time() + 30:  # 30s clock-skew guard
        return tokens.access_token
    if tokens and tokens.refresh_token:
        refreshed = await refresh_tokens(spec.auth, tokens.refresh_token)
        store.save(spec.name, refreshed)
        return refreshed.access_token
    return None


def is_unauthorized(exc: BaseException) -> bool:
    """Heuristic 401 detection: the mcp SDK surfaces HTTP errors as text."""
    text = str(exc)
    return "401" in text or "unauthorized" in text.lower()
