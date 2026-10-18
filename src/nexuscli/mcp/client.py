from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import re
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, contextmanager, suppress
from datetime import timedelta
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from pydantic import AnyUrl

from nexuscli.mcp import auth as mcp_auth
from nexuscli.mcp.config import McpServerSpec, load_mcp_server_specs
from nexuscli.tools.base import Tool, ToolContext, ToolResult, object_schema


class _ServerConnection:
    """One long-lived MCP session, owned by a dedicated task.

    The mcp transports are anyio context managers: entering and exiting them
    from different tasks makes the anyio cancel scope complain ("Attempted to
    exit cancel scope in a different task") and leaks the child process. So the
    owner task enters the session, publishes it, and parks until aclose() asks
    it to leave — enter and exit always happen in the same task.
    """

    def __init__(
        self,
        spec: McpServerSpec,
        session_factory: Callable[[McpServerSpec], Any],
    ):
        self.spec = spec
        self.loop = asyncio.get_running_loop()
        self.lock = asyncio.Lock()
        self._ready: asyncio.Future[Any] = self.loop.create_future()
        self._stop = asyncio.Event()
        self._task = self.loop.create_task(self._run(session_factory))

    async def session(self) -> Any:
        return await self._ready

    @property
    def alive(self) -> bool:
        return not self._task.done()

    async def aclose(self) -> None:
        self._stop.set()
        with suppress(Exception):
            await self._task

    def abandon(self) -> None:
        """Drop a connection whose loop is gone without awaiting its task."""
        self._stop.set()
        with suppress(Exception):
            self._task.cancel()

    async def _run(self, session_factory: Callable[[McpServerSpec], Any]) -> None:
        try:
            async with session_factory(self.spec) as session:
                self._ready.set_result(session)
                await self._stop.wait()
        except asyncio.CancelledError:
            if not self._ready.done():
                self._ready.cancel()
            raise
        except BaseException as exc:  # noqa: BLE001 - surfaced through session()
            if not self._ready.done():
                self._ready.set_exception(exc)
                # The awaiting caller re-raises it; mark it retrieved so an
                # unconsumed future cannot log "exception was never retrieved".
                self._ready.exception()
            return


class McpClientManager:
    def __init__(self, project_root: str | Path):
        self.project_root = str(Path(project_root).resolve())
        self.specs = load_mcp_server_specs(self.project_root)
        self.last_errors: dict[str, str] = {}
        self._connections: dict[str, _ServerConnection] = {}

    async def load_tools(self) -> list[Tool]:
        tools: list[Tool] = []
        self.last_errors.clear()
        for spec in self.specs.values():
            if not spec.enabled:
                continue
            try:
                tools.extend(await self._tools_for_server(spec))
                tools.extend(self._virtual_resource_tools(spec))
                tools.extend(self._virtual_prompt_tools(spec))
            except Exception as exc:  # noqa: BLE001 - keep broken MCP servers isolated
                self.last_errors[spec.name] = str(exc)
        return tools

    async def list_server_tools(self, spec: McpServerSpec) -> list[Any]:
        async def operation(session: Any) -> list[Any]:
            result = await session.list_tools()
            return list(result.tools)

        return await self._with_session(spec, operation)

    async def call_server_tool(
        self,
        spec: McpServerSpec,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        required: tuple[str, ...] = (),
    ) -> ToolResult:
        """Call a remote tool, refusing locally when a required input is absent.

        The remote server happily accepts a null for a required argument (the
        audit's mini server echoed "echo: None"), so a missing key has to fail
        here — before the request — where the message can name the tool and the
        field instead of surfacing as a remote-side surprise.
        """
        missing = [key for key in required if arguments.get(key) is None]
        if missing:
            return ToolResult(
                content=(
                    f'MCP tool "{tool_name}" on server "{spec.name}" is missing required '
                    f"input: {', '.join(missing)}. Not sent."
                ),
                is_error=True,
            )

        async def operation(session: Any) -> ToolResult:
            result = await session.call_tool(
                tool_name,
                arguments,
                read_timeout_seconds=timedelta(seconds=spec.timeout),
            )
            return ToolResult(
                content=_content_to_text(result.content), is_error=bool(result.isError)
            )

        return await self._with_session(spec, operation)

    async def list_resources(self, spec: McpServerSpec) -> ToolResult:
        async def operation(session: Any) -> ToolResult:
            result = await session.list_resources()
            lines = [
                f"{resource.uri} {resource.name or ''} {resource.description or ''}".strip()
                for resource in result.resources
            ]
            return ToolResult("\n".join(lines) or "(no resources)")

        return await self._with_session(spec, operation)

    async def read_resource(self, spec: McpServerSpec, uri: str) -> ToolResult:
        async def operation(session: Any) -> ToolResult:
            result = await session.read_resource(AnyUrl(uri))
            return ToolResult(_content_to_text(result.contents))

        return await self._with_session(spec, operation)

    async def list_prompts(self, spec: McpServerSpec) -> ToolResult:
        async def operation(session: Any) -> ToolResult:
            result = await session.list_prompts()
            lines = [
                f"{prompt.name} {prompt.description or ''}".strip() for prompt in result.prompts
            ]
            return ToolResult("\n".join(lines) or "(no prompts)")

        return await self._with_session(spec, operation)

    async def get_prompt(
        self,
        spec: McpServerSpec,
        name: str,
        arguments: dict[str, str] | None = None,
    ) -> ToolResult:
        async def operation(session: Any) -> ToolResult:
            result = await session.get_prompt(name, arguments or {})
            return ToolResult(_content_to_text(result.messages))

        return await self._with_session(spec, operation)

    async def _with_session(
        self, spec: McpServerSpec, operation: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        """Run one MCP request on the server's long-lived session.

        The session is reused across calls (a fresh server process per call
        lost all server-side state and paid a cold start each time). A server
        that died between calls leaves a dead connection behind, so a single
        rebuild is attempted — but only then: a live session that raised is
        never retried, or a tool with side effects could run twice.
        """
        for attempt in (1, 2):
            connection = await self._connection(spec)
            try:
                session = await connection.session()
            except Exception:
                # Connection setup failed: report it once, exactly like the
                # per-call sessions did (a bad command or a 401 must not turn
                # into a second startup attempt).
                await self._drop(spec.name, connection)
                raise
            try:
                async with connection.lock:
                    return await operation(session)
            except Exception:
                dead = not connection.alive
                await self._drop(spec.name, connection)
                if attempt == 2 or not dead:
                    raise
        raise AssertionError("unreachable: the retry loop always returns or raises")

    async def _connection(self, spec: McpServerSpec) -> _ServerConnection:
        loop = asyncio.get_running_loop()
        connection = self._connections.get(spec.name)
        if connection is not None and connection.loop is not loop:
            # A previous event loop owned that session; its task died with the
            # loop, so only the stale reference is left to forget.
            self._connections.pop(spec.name, None)
            connection = None
        if connection is not None and not connection.alive:
            await self._drop(spec.name, connection)
            connection = None
        if connection is None:
            connection = _ServerConnection(spec, self._session)
            self._connections[spec.name] = connection
        return connection

    async def _drop(self, name: str, connection: _ServerConnection) -> None:
        if self._connections.get(name) is connection:
            self._connections.pop(name, None)
        if connection.loop is asyncio.get_running_loop():
            await connection.aclose()
        else:
            # Owned by another (usually already closed) loop: awaiting it here
            # would hang or raise, so only the stale reference is released.
            connection.abandon()

    async def aclose(self) -> None:
        """Close every open server session, terminating the child processes."""
        for name, connection in list(self._connections.items()):
            await self._drop(name, connection)

    async def _tools_for_server(self, spec: McpServerSpec) -> list[Tool]:
        remote_tools = await self.list_server_tools(spec)
        wrapped: list[Tool] = []
        for remote_tool in remote_tools:
            tool_name = str(remote_tool.name)
            if not self._tool_registered(spec, tool_name):
                continue
            local_name = f"mcp__{spec.name}__{tool_name}"
            schema = remote_tool.inputSchema or object_schema({})
            annotations = getattr(remote_tool, "annotations", None)
            read_only = bool(getattr(annotations, "readOnlyHint", False))
            required = _required_inputs(schema)

            async def handler(
                payload: dict[str, Any],
                context: ToolContext,
                *,
                server_spec: McpServerSpec = spec,
                remote_name: str = tool_name,
                required_inputs: tuple[str, ...] = required,
            ) -> ToolResult:
                _ = context
                return await self.call_server_tool(
                    server_spec, remote_name, payload, required=required_inputs
                )

            wrapped.append(
                Tool(
                    name=local_name,
                    description=remote_tool.description or f"MCP tool {tool_name}",
                    parameters=schema,
                    handler=handler,
                    is_read_only=read_only,
                    is_concurrency_safe=False,
                    danger_level="safe" if read_only else "medium",
                    requires_approval=not read_only,
                )
            )
        return wrapped

    @staticmethod
    def _tool_registered(spec: McpServerSpec, tool_name: str) -> bool:
        """Whether a remote tool passes this server's per-tool filters.

        ``allowed_tools`` is a whitelist and takes priority: when it is
        non-empty, a tool must match at least one pattern. Otherwise, if
        ``blocked_tools`` is non-empty, a matching tool is excluded. Patterns
        match exactly by name or via fnmatch wildcards. With no filters
        configured, every tool is registered (unchanged behavior).
        """
        if spec.allowed_tools:
            return any(_tool_matches(pattern, tool_name) for pattern in spec.allowed_tools)
        if spec.blocked_tools:
            return not any(_tool_matches(pattern, tool_name) for pattern in spec.blocked_tools)
        return True

    def _virtual_resource_tools(self, spec: McpServerSpec) -> list[Tool]:
        async def list_handler(payload: dict[str, Any], context: ToolContext) -> ToolResult:
            _ = payload, context
            return await self.list_resources(spec)

        async def read_handler(payload: dict[str, Any], context: ToolContext) -> ToolResult:
            _ = context
            return await self.read_resource(spec, str(payload["uri"]))

        return [
            Tool(
                name=f"mcp__{spec.name}__list_resources",
                description=f"List MCP resources from {spec.name}.",
                parameters=object_schema({}),
                handler=list_handler,
                is_read_only=True,
            ),
            Tool(
                name=f"mcp__{spec.name}__read_resource",
                description=f"Read an MCP resource from {spec.name}.",
                parameters=object_schema(
                    {"uri": {"type": "string", "description": "Resource URI"}},
                    ["uri"],
                ),
                required_keys=["uri"],
                handler=read_handler,
                is_read_only=True,
            ),
        ]

    def _virtual_prompt_tools(self, spec: McpServerSpec) -> list[Tool]:
        async def list_handler(payload: dict[str, Any], context: ToolContext) -> ToolResult:
            _ = payload, context
            return await self.list_prompts(spec)

        async def get_handler(payload: dict[str, Any], context: ToolContext) -> ToolResult:
            _ = context
            arguments = payload.get("arguments")
            if arguments is not None and not isinstance(arguments, dict):
                return ToolResult("arguments must be an object", is_error=True)
            return await self.get_prompt(
                spec,
                str(payload["name"]),
                {str(k): str(v) for k, v in (arguments or {}).items()},
            )

        return [
            Tool(
                name=f"mcp__{spec.name}__list_prompts",
                description=f"List MCP prompts from {spec.name}.",
                parameters=object_schema({}),
                handler=list_handler,
                is_read_only=True,
            ),
            Tool(
                name=f"mcp__{spec.name}__get_prompt",
                description=f"Get an MCP prompt from {spec.name}.",
                parameters=object_schema(
                    {
                        "name": {"type": "string", "description": "Prompt name"},
                        "arguments": {"type": "object", "description": "Prompt arguments"},
                    },
                    ["name"],
                ),
                required_keys=["name"],
                handler=get_handler,
                is_read_only=True,
            ),
        ]

    @asynccontextmanager
    async def _session(self, spec: McpServerSpec):
        if spec.type in {"stdio", "local"}:
            if not spec.command:
                raise ValueError(f"MCP server {spec.name} is missing command")
            params = StdioServerParameters(
                command=spec.command,
                args=spec.args,
                env={**os.environ, **spec.env},
                cwd=spec.cwd or self.project_root,
            )
            with self._stderr_log(spec) as errlog:
                async with (
                    stdio_client(params, errlog=errlog) as (read, write),
                    ClientSession(read, write) as session,
                ):
                    await session.initialize()
                    yield session
            return
        if spec.type in {"http", "streamable_http", "streamable-http"}:
            if not spec.url:
                raise ValueError(f"MCP server {spec.name} is missing url")
            headers = dict(spec.headers or {})
            token = await mcp_auth.ensure_access_token(spec)
            if token:
                headers["Authorization"] = f"Bearer {token}"
            # One attempt, then at most one 401-triggered refresh+retry. Failures
            # after the session was handed to the consumer cannot be retried here.
            entered = False
            try:
                async with self._http_session_once(spec, headers) as session:
                    entered = True
                    yield session
                return
            except Exception as exc:
                if entered or spec.auth is None or not mcp_auth.is_unauthorized(exc):
                    raise
                retry_headers = await self._refreshed_headers(spec, dict(spec.headers or {}))
                if retry_headers is None:
                    raise  # no refresh token or refresh failed: original 401 wins
                async with self._http_session_once(spec, retry_headers) as session:
                    yield session
                return
        raise ValueError(f"Unsupported MCP transport: {spec.type}")

    @asynccontextmanager
    async def _http_session_once(self, spec: McpServerSpec, headers: dict[str, str] | None):
        """A single connect + initialize attempt over the streamable-http transport."""
        async with (
            streamablehttp_client(
                spec.url,
                headers=headers or None,
                timeout=spec.timeout,
            ) as (read, write, _session_id),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session

    async def _refreshed_headers(
        self, spec: McpServerSpec, base: dict[str, str]
    ) -> dict[str, str] | None:
        """Refresh tokens and build full retry headers; None when impossible."""
        cfg = spec.auth
        if cfg is None:
            return None
        store = mcp_auth.TokenStore()
        existing = store.tokens_for(spec.name)
        if existing is None or not existing.refresh_token:
            return None
        try:
            tokens = await mcp_auth.refresh_tokens(cfg, existing.refresh_token)
        except Exception:  # a failed refresh must not mask the original 401
            return None
        store.save(spec.name, tokens)
        return {**base, "Authorization": f"Bearer {tokens.access_token}"}

    @contextmanager
    def _stderr_log(self, spec: McpServerSpec):
        """Keep server stderr out of the CLI's stderr without losing it entirely.

        Stdio servers commonly print banners and warnings on startup, so their
        output goes to a per-server log file for debugging broken servers; if the
        log location is unwritable, fall back to discarding it.
        """
        log_dir = Path(self.project_root) / ".nexuscli" / "mcp-logs"
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", spec.name)
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            path: str = str(log_dir / f"{safe_name}.log")
        except OSError:
            path = os.devnull
        with open(path, "a", encoding="utf-8") as handle:
            yield handle


def _required_inputs(schema: Any) -> tuple[str, ...]:
    """Required argument names declared by a remote tool's JSON input schema.

    Only top-level ``required`` names are collected: nested object properties
    are the remote server's business, while a missing top-level argument is
    exactly the case that used to reach the server as an explicit null.
    """
    if not isinstance(schema, dict):
        return ()
    required = schema.get("required")
    if not isinstance(required, (list, tuple)):
        return ()
    return tuple(str(name) for name in required)


def _tool_matches(pattern: str, tool_name: str) -> bool:
    return pattern == tool_name or fnmatch.fnmatchcase(tool_name, pattern)


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(filter(None, (_content_to_text(item) for item in content)))
    if hasattr(content, "text"):
        return str(content.text)
    if hasattr(content, "data") and hasattr(content, "mimeType"):
        data = str(content.data)
        return f"[image {content.mimeType} base64 chars={len(data)}]"
    if hasattr(content, "resource"):
        return _content_to_text(content.resource)
    if hasattr(content, "model_dump"):
        return json.dumps(content.model_dump(mode="json"), ensure_ascii=False)
    return str(content)
