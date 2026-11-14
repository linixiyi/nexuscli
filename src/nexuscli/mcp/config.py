from __future__ import annotations

import json
import os
import re
import sys
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexuscli.hooks.trust import HookTrustStore, hooks_fingerprint


@dataclass(slots=True)
class McpAuthConfig:
    """OAuth 2.0 (authorization code + PKCE, public client) settings.

    Public client by design: there is no ``client_secret`` field on purpose —
    machine secrets must never be written into mcp.json; real credentials only
    arrive via the environment or the OAuth flow itself. The ``redirect_uri``
    default is a placeholder: this slice never starts a callback listener.
    """

    type: str = "oauth"
    client_id: str = ""
    authorize_url: str = ""
    token_url: str = ""
    redirect_uri: str = "http://localhost:8765/callback"
    scopes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class McpServerSpec:
    name: str
    type: str = "stdio"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    timeout: float = 30.0
    auth: McpAuthConfig | None = None
    # Per-server tool filters. ``allowed_tools`` is a whitelist and wins over
    # ``blocked_tools``; both support exact names and fnmatch wildcards. Empty
    # lists mean no filtering (all remote tools registered, as before).
    allowed_tools: list[str] = field(default_factory=list)
    blocked_tools: list[str] = field(default_factory=list)


class McpConfigError(ValueError):
    """A readable ``mcp.json`` that cannot be parsed or has the wrong shape.

    Silent tolerance here was actively misleading: a typo in the JSON made
    ``mcp list`` print "No MCP servers configured.", so the user went looking
    for a missing file or the wrong scope instead of the broken one.
    """


class McpTrustWarning(UserWarning):
    """Raised (via ``warnings.warn``) when untrusted project-layer MCP servers are ignored."""


MCP_TRUST_STORE_FILENAME = "mcp-trust.json"


def _user_mcp_path() -> Path:
    """The user-layer config; resolved at call time so HOME redirects (tests) apply."""
    return Path.home() / ".nexuscli" / "mcp.json"


def load_mcp_server_specs(
    project_root: str | Path, *, confirm: Callable[[str], bool] | None = None
) -> dict[str, McpServerSpec]:
    """Merge the user-layer and (trusted) project-layer servers into specs.

    The user layer (``~/.nexuscli/mcp.json``) always applies. The project layer
    (``<root>/.nexuscli/mcp.json``) only merges when it passes the project
    trust gate — see :func:`_project_layer_servers`; untrusted, only user-layer
    servers survive. ``confirm`` is the injection seam for tests and embedders;
    the default prompts on a TTY and fails closed without one (headless ``-p``
    and serve never trust).
    """
    root = Path(project_root).resolve()
    user_path = _user_mcp_path()
    merged: dict[str, Any] = {}
    for path in [user_path, root / ".nexuscli" / "mcp.json"]:
        data = _read_json(path)
        if not data:
            continue
        servers = data.get("mcpServers", data)
        if isinstance(servers, dict):
            if path != user_path:
                servers = _project_layer_servers(servers, root, confirm)
            merged.update(servers)
    return {
        name: _spec_from_raw(name, raw, root)
        for name, raw in merged.items()
        if isinstance(raw, dict)
    }


def read_mcp_config_entries(
    project_root: str | Path, *, confirm: Callable[[str], bool] | None = None
) -> tuple[dict[str, McpServerSpec], list[str]]:
    """Like :func:`load_mcp_server_specs`, but report broken config files.

    Returns ``(specs, skipped)``. Raises :class:`McpConfigError` naming the
    offending path when an existing ``mcp.json`` is unparseable or not a JSON
    object — a missing file stays a normal "no configuration" case. Entries
    that are not objects are listed in ``skipped`` so ``mcp list`` can say
    which name was dropped instead of silently narrowing the table. The
    project layer goes through the same trust gate as
    :func:`load_mcp_server_specs` (same fingerprint, same store), so trusting
    via ``mcp list`` also unblocks the runtime loader.
    """
    root = Path(project_root).resolve()
    user_path = _user_mcp_path()
    merged: dict[str, Any] = {}
    skipped: list[str] = []
    for path in [user_path, root / ".nexuscli" / "mcp.json"]:
        data = _read_json(path, strict=True)
        if not data:
            continue
        servers = data.get("mcpServers", data)
        if not isinstance(servers, dict):
            raise McpConfigError(f'{path}: "mcpServers" must be a JSON object')
        if path != user_path:
            servers = _project_layer_servers(servers, root, confirm)
        for name, raw in servers.items():
            if isinstance(raw, dict):
                merged[name] = raw
            else:
                skipped.append(f"{name}: not an object")
    specs = {name: _spec_from_raw(name, raw, root) for name, raw in merged.items()}
    return specs, skipped


def _project_layer_servers(
    servers: dict[str, Any], root: Path, confirm: Callable[[str], bool] | None
) -> dict[str, Any]:
    """Gate the project-layer ``mcpServers`` mapping through project trust.

    An enabled project server runs an arbitrary local command from a cloned
    repository, so the whole layer needs a trust decision before any of it
    merges: the fingerprint is canonical JSON + sha256 over the mapping (the
    pinned form of :func:`nexuscli.hooks.trust.hooks_fingerprint`), remembered
    per workspace in ``~/.nexuscli/mcp-trust.json`` — a store file of its own,
    so revoking config/hooks trust never silently re-enables project MCP
    servers (and vice versa). Untrusted layers are dropped entirely (only
    user-layer servers survive) after one visible warning naming what was
    ignored; nothing is remembered on refusal.

    A layer with only disabled servers can never spawn and merges exactly as
    before — no gate, no warning. Headless entrypoints (``-p``, serve) run
    without a TTY and therefore fail closed, matching the config-layer gate.
    """
    if not servers:
        return {}
    if not any(
        isinstance(raw, dict) and bool(raw.get("enabled", True)) for raw in servers.values()
    ):
        return servers
    fingerprint_value = hooks_fingerprint(servers)
    store = HookTrustStore(Path.home() / ".nexuscli" / MCP_TRUST_STORE_FILENAME)
    workspace = str(root)
    if store.is_trusted(workspace, fingerprint_value):
        return servers

    description = f"{workspace} (fingerprint {fingerprint_value[:16]})"
    if confirm is not None:
        trusted = confirm(description)
    elif sys.stdin.isatty():
        trusted = _interactive_confirm(description)
    else:
        trusted = False
    if trusted:
        store.trust(workspace, fingerprint_value)
        return servers

    names = ", ".join(sorted(str(name) for name in servers))
    warnings.warn(
        (
            f"Untrusted project layer at {workspace}: ignoring project-layer MCP servers "
            f"({names}). Start nexuscli interactively to review and trust this project."
        ),
        McpTrustWarning,
        stacklevel=3,
    )
    return {}


def _interactive_confirm(description: str) -> bool:
    """Prompt once on the TTY; a closed or unread stdin refuses (fail-closed)."""
    print(
        f"[project trust] Project-layer MCP servers detected: {description}",
        file=sys.stderr,
    )
    try:
        answer = input("Trust this project's MCP servers? [y/N] ")
    except (EOFError, OSError):
        return False
    return answer.strip().lower() in {"y", "yes"}


def write_chrome_devtools_config(
    *,
    scope_root: str | Path | None = None,
    browser_url: str | None = None,
    headless: bool = False,
    slim: bool = False,
    no_usage_statistics: bool = True,
) -> Path:
    root = Path(scope_root).resolve() if scope_root else Path.home()
    config_dir = root / ".nexuscli"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "mcp.json"
    data = _read_json(path) or {"mcpServers": {}}
    servers = data.setdefault("mcpServers", {})
    args = ["-y", "chrome-devtools-mcp@latest"]
    if no_usage_statistics:
        args.append("--no-usage-statistics")
    if slim:
        args.append("--slim")
    if headless:
        args.append("--headless")
    if browser_url:
        args.append(f"--browser-url={browser_url}")
    servers["chrome-devtools"] = {
        "type": "stdio",
        "command": "npx",
        "args": args,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _read_json(path: Path, *, strict: bool = False) -> dict[str, Any] | None:
    """Read a JSON object; ``None`` when the file is absent or unusable.

    ``strict`` turns the two "file exists but is broken" cases into
    :class:`McpConfigError` naming the path, which is what the CLI needs to
    stop pretending a corrupt config means "nothing configured".
    """
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        if strict:
            raise McpConfigError(f"{path}: cannot read config: {exc}") from exc
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        if strict:
            raise McpConfigError(f"{path}: invalid JSON: {exc}") from exc
        return None
    if not isinstance(value, dict):
        if strict:
            raise McpConfigError(f"{path}: config must be a JSON object")
        return None
    return value


def _spec_from_raw(name: str, raw: dict[str, Any], project_root: Path) -> McpServerSpec:
    default_type = "streamable_http" if raw.get("url") else "stdio"
    server_type = str(raw.get("type") or raw.get("transport") or default_type)
    env = {
        key: _expand(str(value), project_root) for key, value in dict(raw.get("env") or {}).items()
    }
    args = [_expand(str(arg), project_root) for arg in raw.get("args") or []]
    cwd = raw.get("cwd")
    return McpServerSpec(
        name=name,
        type=server_type,
        command=_expand(str(raw["command"]), project_root) if raw.get("command") else None,
        args=args,
        env=env,
        cwd=_expand(str(cwd), project_root) if cwd else None,
        url=_expand(str(raw["url"]), project_root) if raw.get("url") else None,
        headers={
            key: _expand(str(value), project_root)
            for key, value in dict(raw.get("headers") or {}).items()
        },
        enabled=bool(raw.get("enabled", True)),
        timeout=float(raw.get("timeout", raw.get("startup_timeout", 30.0)) or 30.0),
        auth=_auth_from_raw(raw.get("auth"), project_root),
        allowed_tools=_tool_filter_list(raw.get("allowed_tools")),
        blocked_tools=_tool_filter_list(raw.get("blocked_tools")),
    )


def _tool_filter_list(value: Any) -> list[str]:
    """Parse an ``allowed_tools``/``blocked_tools`` config value.

    Only a real list (or tuple) is accepted — anything else, most notably a
    plain string, means "not configured" and degrades to no filtering. A bare
    string must not be iterated: ``"echo"`` would become the whitelist
    ``["e", "c", "h", "o"]``, silently hiding every remote tool on the server
    with no error at all.
    """
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value]


def _auth_from_raw(raw: Any, project_root: Path) -> McpAuthConfig | None:
    """Parse the optional ``auth`` section; unknown or invalid shapes are ignored."""
    if not isinstance(raw, dict):
        return None
    if str(raw.get("type") or "oauth") != "oauth":
        return None  # unknown auth types are safely ignored
    default_redirect = "http://localhost:8765/callback"
    return McpAuthConfig(
        client_id=_expand(str(raw.get("client_id", "")), project_root),
        authorize_url=_expand(str(raw.get("authorize_url", "")), project_root),
        token_url=_expand(str(raw.get("token_url", "")), project_root),
        redirect_uri=_expand(str(raw.get("redirect_uri") or default_redirect), project_root),
        scopes=[str(scope) for scope in raw.get("scopes") or []],
    )


def _expand(value: str, project_root: Path) -> str:
    replacements = {
        "PROJECT_DIR": str(project_root),
        "HOME": str(Path.home()),
    }

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        return replacements.get(name, os.environ.get(name, ""))

    return re.sub(r"\$\{([^}]+)\}", replace, value)
