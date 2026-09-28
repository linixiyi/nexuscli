from __future__ import annotations

import json
import os
from contextlib import suppress
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def _home() -> Path:
    return Path.home()


@dataclass(slots=True)
class LlmConfig:
    provider: str = "deepseek"
    model: str = "deepseek-v4-flash"
    api_key: str = ""
    base_url: str | None = None
    context_window: int | None = None
    # Optional per-million-token overrides, keyed by currency then
    # input_cache_hit/input_cache_miss/output. Provider prices can change.
    prices: dict[str, dict[str, float]] = field(default_factory=dict)
    max_tokens: int = 8192
    temperature: float = 0.7
    timeout: float = 120.0


@dataclass(slots=True)
class ToolsConfig:
    enabled: list[str] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)
    timeout: float = 60.0
    batch_timeout: float = 90.0
    max_concurrent_read: int = 4


@dataclass(slots=True)
class McpConfig:
    servers: list[dict[str, Any]] = field(default_factory=list)
    auto_start: bool = True


@dataclass(slots=True)
class MemoryConfig:
    max_conversation_history: int = 100
    long_term_enabled: bool = True
    long_term_db_path: str = "~/.nexuscli/memory.db"
    max_long_term_entries: int = 1_000
    max_memory_chars: int = 8_000
    recall_limit: int = 6
    recall_min_score: float = 0.05
    token_budget_mode: str = "balanced"
    compression_threshold: float = 0.8
    compression_target: float = 0.55
    compression_reserve_tokens: int = 1_024
    min_recent_messages: int = 6
    summary_max_chars: int = 6_000


@dataclass(slots=True)
class PolicyConfig:
    hitl_mode: str = "auto"
    path_guard_enabled: bool = True
    command_guard_enabled: bool = True
    command_blacklist: list[str] = field(
        default_factory=lambda: [
            "sudo",
            "rm -rf /",
            "rm -rf ~",
            "mkfs",
            "dd if=/dev/zero",
            ":(){:|:&};:",
            "chmod -R 777 /",
            "curl | sh",
            "curl|sh",
            "shutdown",
            "reboot",
        ]
    )
    audit_log_path: str = "~/.nexuscli/audit.jsonl"
    # Runtime session stamp, rewritten by the Agent at startup the same way
    # hitl_mode is rewritten live by the permission-mode controller.
    session_id: str = ""
    # Runtime plan-mode flag, flipped by the permission-mode controller. When
    # True the executor hard-rejects every non-read-only tool call.
    plan_mode: bool = False


@dataclass(slots=True)
class PermissionsConfig:
    """Pattern-based permission rules (deny > ask > allow), evaluated before HITL."""

    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    ask: list[str] = field(default_factory=list)


# Lifecycle hook events as they appear in config.json, mapped to the
# snake_case fields of :class:`HooksConfig`. Shared with the hooks package so
# config parsing and event lookup never drift apart.
HOOK_EVENT_FIELDS: dict[str, str] = {
    "SessionStart": "session_start",
    "UserPromptSubmit": "user_prompt_submit",
    "PreToolUse": "pre_tool_use",
    "PostToolUse": "post_tool_use",
    "Stop": "stop",
}


@dataclass(slots=True)
class HookCommandConfig:
    """One hook command (Claude Code style); only ``type="command"`` exists."""

    type: str = "command"
    command: str = ""
    timeout: int = 60  # seconds


@dataclass(slots=True)
class HookMatcherConfig:
    """A matcher group: regex over the tool name plus the hooks it selects."""

    matcher: str = "*"  # regex matched against tool names; tool events only
    hooks: list[HookCommandConfig] = field(default_factory=list)


@dataclass(slots=True)
class HooksConfig:
    """Lifecycle hooks, keyed by event in config.json (camelCase keys)."""

    session_start: list[HookMatcherConfig] = field(default_factory=list)
    user_prompt_submit: list[HookMatcherConfig] = field(default_factory=list)
    pre_tool_use: list[HookMatcherConfig] = field(default_factory=list)
    post_tool_use: list[HookMatcherConfig] = field(default_factory=list)
    stop: list[HookMatcherConfig] = field(default_factory=list)


@dataclass(slots=True)
class PromptConfig:
    personality: str = "default"
    agent_mode: str = "react"
    custom_prompt_paths: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FeatureConfig:
    mcp: bool = True
    skill: bool = True
    memory: bool = True
    audit_log: bool = True
    context_compression: bool = True
    code_index: bool = True


@dataclass(slots=True)
class AgentConfig:
    """Agent loop settings for long-running tasks.

    ``max_turns`` caps the react tool loop per user message; the default of
    200 favours long-running tasks over a low hard stop. At load time values
    are clamped to at least 1 (0 or a negative number would turn every run
    into a no-op; see :func:`_dict_to_agent`).
    """

    max_turns: int = 200


@dataclass(slots=True)
class NexusCliConfig:
    llm: LlmConfig = field(default_factory=LlmConfig)
    render_mode: str = "inline"
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    mcp: McpConfig = field(default_factory=McpConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    permissions: PermissionsConfig = field(default_factory=PermissionsConfig)
    hooks: HooksConfig = field(default_factory=HooksConfig)
    prompt: PromptConfig = field(default_factory=PromptConfig)
    features: FeatureConfig = field(default_factory=FeatureConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)


def load_config(
    project_root: str | Path | None = None,
    overrides: dict[str, Any] | None = None,
    env: dict[str, str | None] | None = None,
) -> NexusCliConfig:
    env_map = env if env is not None else os.environ
    data = _config_to_dict(NexusCliConfig())

    user_config = _read_json(_home() / ".nexuscli" / "config.json")
    if user_config:
        data = _deep_merge(data, user_config)

    root = Path(project_root).resolve() if project_root else None
    if root:
        project_config = _read_json(root / ".nexuscli" / "config.json")
        if project_config:
            data = _deep_merge(data, project_config)
            data = _merge_permission_lists(data, user_config, project_config)
            data = _merge_hook_lists(data, user_config, project_config)
        project_env = _read_env(root / ".env")
        if project_env:
            data = _apply_env(data, project_env)

    if overrides:
        data = _deep_merge(data, overrides)

    data = _apply_env(data, env_map)
    config = _dict_to_config(data)
    config.memory.long_term_db_path = _expand_home(config.memory.long_term_db_path)
    config.policy.audit_log_path = _expand_home(config.policy.audit_log_path)
    return config


def get_config_paths(project_root: str | Path | None = None) -> list[Path]:
    paths = [_home() / ".nexuscli" / "config.json"]
    if project_root:
        paths.append(Path(project_root).resolve() / ".nexuscli" / "config.json")
    return paths


def config_to_public_dict(config: NexusCliConfig) -> dict[str, Any]:
    data = _config_to_dict(config)
    if data.get("llm", {}).get("api_key"):
        data["llm"]["api_key"] = "***"
    return data


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _read_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return result
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        result[key] = value
    return result


def _apply_env(data: dict[str, Any], env: dict[str, str | None]) -> dict[str, Any]:
    result = deepcopy(data)
    llm = result.setdefault("llm", {})
    features = result.setdefault("features", {})
    policy = result.setdefault("policy", {})

    mappings: list[tuple[str, str, Any]] = [
        ("NEXUSCLI_API_KEY", "api_key", str),
        ("NEXUSCLI_PROVIDER", "provider", str),
        ("NEXUSCLI_MODEL", "model", str),
        ("NEXUSCLI_BASE_URL", "base_url", str),
        ("NEXUSCLI_CONTEXT_WINDOW", "context_window", int),
        ("NEXUSCLI_MAX_TOKENS", "max_tokens", int),
        ("NEXUSCLI_TEMPERATURE", "temperature", float),
    ]
    for env_key, config_key, caster in mappings:
        raw = env.get(env_key)
        if raw not in (None, ""):
            with suppress(TypeError, ValueError):
                llm[config_key] = caster(raw)

    provider = str(llm.get("provider") or "").lower()
    if not llm.get("api_key"):
        provider_key_map = {
            "deepseek": ("DEEPSEEK_API_KEY",),
            "glm": ("ZAI_API_KEY", "GLM_API_KEY"),
            "zhipu": ("ZAI_API_KEY", "GLM_API_KEY"),
            "step": ("STEP_API_KEY",),
            "kimi": ("KIMI_API_KEY",),
            "moonshot": ("KIMI_API_KEY",),
            "freellmapi": ("FREELLMAPI_API_KEY",),
            "xfyun": ("XFYUN_API_KEY",),
            "agnes": ("AGNES_API_KEY",),
        }
        for provider_key in provider_key_map.get(provider, ()):
            if env.get(provider_key):
                llm["api_key"] = env[provider_key]
                break

    provider_model_key = f"{provider.upper()}_MODEL" if provider else ""
    provider_base_url_key = f"{provider.upper()}_BASE_URL" if provider else ""
    if provider_model_key and env.get(provider_model_key):
        llm["model"] = env[provider_model_key]
    if provider_base_url_key and env.get(provider_base_url_key):
        llm["base_url"] = env[provider_base_url_key]

    render_mode = env.get("NEXUSCLI_RENDER_MODE") or env.get("NEXUSCLI_RENDERER")
    if render_mode in {"plain", "inline"}:
        result["render_mode"] = render_mode

    if env.get("NEXUSCLI_TUI") == "true":
        result["render_mode"] = "inline"

    for env_key, feature_key in [
        ("NEXUSCLI_MCP", "mcp"),
        ("NEXUSCLI_SKILL", "skill"),
        ("NEXUSCLI_MEMORY", "memory"),
    ]:
        raw = env.get(env_key)
        if raw == "false":
            features[feature_key] = False
        elif raw == "true":
            features[feature_key] = True

    hitl = env.get("NEXUSCLI_HITL")
    if hitl in {"always", "auto", "never"}:
        policy["hitl_mode"] = hitl

    return result


def _deep_merge(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(target)
    for key, value in source.items():
        if value is None:
            continue
        old = result.get(key)
        if isinstance(old, dict) and isinstance(value, dict):
            result[key] = _deep_merge(old, value)
        else:
            result[key] = deepcopy(value)
    return result


def _merge_permission_lists(
    data: dict[str, Any],
    user_config: dict[str, Any] | None,
    project_config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Concatenate permission rule lists across config layers.

    Generic deep-merge replaces lists, but permission rules must append: a
    project config defining ``permissions.allow`` must never silently erase
    the user-level ``deny`` list.
    """

    def _str_list(raw: Any) -> list[str]:
        if not isinstance(raw, list):
            return []
        return [str(item).strip() for item in raw if str(item).strip()]

    merged = data.setdefault("permissions", {})
    for action in ("allow", "deny", "ask"):
        user_rules = _str_list((user_config or {}).get("permissions", {}).get(action))
        project_rules = _str_list((project_config or {}).get("permissions", {}).get(action))
        combined = [*user_rules, *project_rules]
        if combined:
            merged[action] = list(dict.fromkeys(combined))
    return data


def _merge_hook_lists(
    data: dict[str, Any],
    user_config: dict[str, Any] | None,
    project_config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Concatenate hook matcher lists across config layers.

    Same story as permission rules: generic deep-merge replaces lists, so a
    project config defining ``hooks.PreToolUse`` would silently erase the
    user-level hooks for that event. User matchers come first, project
    matchers are appended after them.
    """

    def _matchers(config: dict[str, Any] | None, event_key: str) -> list[Any]:
        section = (config or {}).get("hooks")
        if not isinstance(section, dict):
            return []
        items = section.get(event_key)
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    merged = data.setdefault("hooks", {})
    for event_key in HOOK_EVENT_FIELDS:
        combined = [
            *_matchers(user_config, event_key),
            *_matchers(project_config, event_key),
        ]
        if combined:
            merged[event_key] = combined
    return data


def _config_to_dict(config: NexusCliConfig) -> dict[str, Any]:
    return asdict(config)


def _dict_to_config(data: dict[str, Any]) -> NexusCliConfig:
    # Unknown keys (e.g. typos in config.json) are dropped instead of raising,
    # so one bad entry cannot prevent the CLI from starting.
    return NexusCliConfig(
        llm=LlmConfig(**_filter_known(data.get("llm", {}), LlmConfig)),
        render_mode=data.get("render_mode", "inline"),
        tools=ToolsConfig(**_filter_known(data.get("tools", {}), ToolsConfig)),
        mcp=McpConfig(**_filter_known(data.get("mcp", {}), McpConfig)),
        memory=MemoryConfig(**_filter_known(data.get("memory", {}), MemoryConfig)),
        policy=PolicyConfig(**_filter_known(data.get("policy", {}), PolicyConfig)),
        permissions=PermissionsConfig(
            **_filter_known(data.get("permissions", {}), PermissionsConfig)
        ),
        hooks=_dict_to_hooks(data.get("hooks")),
        prompt=PromptConfig(**_filter_known(data.get("prompt", {}), PromptConfig)),
        features=FeatureConfig(**_filter_known(data.get("features", {}), FeatureConfig)),
        agent=_dict_to_agent(data.get("agent")),
    )


def _dict_to_agent(raw: Any) -> AgentConfig:
    """Parse the ``agent`` config section, clamping ``max_turns`` to >= 1.

    A zero or negative ``max_turns`` would make every agent run a no-op, so
    it is clamped to 1 at load time; non-numeric values fall back to the
    default instead of raising, so one bad entry cannot prevent the CLI from
    starting.
    """
    if not isinstance(raw, dict):
        return AgentConfig()
    fields = _filter_known(raw, AgentConfig)
    default = AgentConfig()
    try:
        fields["max_turns"] = max(1, int(fields.get("max_turns", default.max_turns)))
    except (TypeError, ValueError):
        fields["max_turns"] = default.max_turns
    return AgentConfig(**fields)


def _dict_to_hooks(raw: Any) -> HooksConfig:
    """Parse the ``hooks`` config section.

    Keys are camelCase event names (``PreToolUse``) mapping to snake_case
    ``HooksConfig`` fields; unknown events and malformed entries are dropped
    instead of raising, so one bad entry cannot prevent the CLI from starting.
    """
    if not isinstance(raw, dict):
        return HooksConfig()
    fields: dict[str, list[HookMatcherConfig]] = {}
    for event_key, matchers in raw.items():
        field_name = HOOK_EVENT_FIELDS.get(str(event_key))
        if field_name is None or not isinstance(matchers, list):
            continue
        fields[field_name] = [_dict_to_matcher(item) for item in matchers if isinstance(item, dict)]
    return HooksConfig(**fields)


def _dict_to_matcher(raw: dict[str, Any]) -> HookMatcherConfig:
    fields = _filter_known(raw, HookMatcherConfig)
    hooks_raw = raw.get("hooks")
    fields["hooks"] = [
        HookCommandConfig(**_filter_known(item, HookCommandConfig))
        for item in (hooks_raw if isinstance(hooks_raw, list) else [])
        if isinstance(item, dict)
    ]
    return HookMatcherConfig(**fields)


def _filter_known(raw: Any, cls: type) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
    return {key: value for key, value in raw.items() if key in known}


def _expand_home(path: str) -> str:
    return str(Path(path).expanduser())
