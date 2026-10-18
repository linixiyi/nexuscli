"""model_cmds.py — /model command: interactive selector, live activation, provider tables."""

from __future__ import annotations

import dataclasses

from rich.console import Console
from rich.prompt import Prompt

from nexuscli.agent import Agent
from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints.model_selector import ModelSelectorState, run_model_selector
from nexuscli.llm import create_llm_client
from nexuscli.llm.model_profiles import (
    DEFAULT_MODEL_PROFILES,
    PROVIDER_API_KEY_ENVS,
    PROVIDER_DEFAULTS,
    CustomModelStore,
    ModelProfile,
)
from nexuscli.llm.openai_compatible import ModelFetchError, fetch_available_models
from nexuscli.prompt import PromptAssembler
from nexuscli.render import RichRenderer
from nexuscli.tools import ToolRegistry


async def _model_command(
    arg: str,
    console: Console,
    cwd: str,
    config: NexusCliConfig,
    agent: Agent,
    registry: ToolRegistry,
    renderer: RichRenderer,
) -> None:
    if arg:
        parts = arg.split(maxsplit=1)
        provider = config.llm.provider if len(parts) == 1 else parts[0]
        model = parts[0] if len(parts) == 1 else parts[1]
        profile = next(
            (
                item
                for item in DEFAULT_MODEL_PROFILES
                if item.provider == provider.lower() and item.model == model
            ),
            None,
        )
        if profile is None:
            base_url = (
                config.llm.base_url
                if len(parts) == 1 and config.llm.base_url
                else _provider_defaults(provider)[1]
            )
            profile = ModelProfile(
                id="command-line-selection",
                name=model,
                provider=provider,
                model=model,
                base_url=base_url,
                context_window=config.llm.context_window or 128_000,
                description="Selected from /model arguments",
                api_key_env=_provider_api_key_env(provider),
            )
        _activate_model(profile, config, agent, registry, renderer, cwd)
        console.print(f"[green]Switched model:[/green] {model} ({provider})")
        return

    store = CustomModelStore()
    fetched: list = []
    while True:
        state = ModelSelectorState(
            defaults=list(DEFAULT_MODEL_PROFILES),
            custom=store.list(),
            current_provider=agent.llm_client.provider_name,
            current_model=agent.llm_client.model_name,
            fetch=fetched,
            fetch_base_url=config.llm.base_url or "",
        )
        action = await run_model_selector(state)
        if action is None:
            return
        if action.kind == "add":
            profile = _prompt_custom_model(console)
            if profile is not None:
                store.add(profile)
                _activate_model(profile, config, agent, registry, renderer, cwd)
                console.print(
                    f"[green]Saved and switched to custom model:[/green] {profile.name} "
                    f"[dim]({store.path})[/dim]"
                )
                return
            continue
        if action.kind == "refetch":
            fetched = _prompt_fetch_models(console, config)
            continue
        if action.kind == "add_fetched" and action.fetched:
            added = _store_fetched_profiles(
                store, list(action.fetched), config, agent, registry, renderer, cwd, console
            )
            if added:
                return
            continue
        if action.kind == "delete" and action.profile is not None:
            if store.delete(action.profile.id):
                console.print(f"Deleted custom model: {action.profile.name}")
            continue
        if action.profile is not None:
            _activate_model(action.profile, config, agent, registry, renderer, cwd)
            console.print(
                f"[green]Switched model:[/green] {action.profile.name} "
                f"[dim]({action.profile.provider}/{action.profile.model})[/dim]"
            )
            return


def _prompt_fetch_models(console: Console, config: NexusCliConfig) -> list:
    """Ask for a base URL, pull ``GET /models``, return listing profiles.

    Blocking prompt + network call deliberately live here instead of inside
    the prompt_toolkit application: the selector only renders state, this
    function mutates it between renders (same split as the add-custom flow).
    """
    default_url = config.llm.base_url or config_base_url(config.llm.provider)
    console.print("\n[bold]从 API 拉取模型列表[/bold]")
    console.print("[dim]OpenAI Chat Completions 协议；密钥取自当前 provider 配置。[/dim]")
    base_url = Prompt.ask("API 地址", default=default_url).strip().rstrip("/")
    api_key = config.llm.api_key
    try:
        models = fetch_available_models(base_url, api_key)
    except ModelFetchError as exc:
        console.print(f"[red]拉取失败：[/red]{exc.reason}")
        console.print("[dim]可回到选择器按 r 重试。[/dim]")
        return []
    console.print(f"[green]拉取成功：[/green]{len(models)} 个模型")
    for item in models:
        item.base_url = base_url
    return models


def _store_fetched_profiles(
    store: CustomModelStore,
    listings: list,
    config: NexusCliConfig,
    agent: Agent,
    registry: ToolRegistry,
    renderer: RichRenderer,
    cwd: str,
    console: Console,
) -> bool:
    """Persist chosen fetched listings as custom profiles and switch to the first.

    All chosen rows land in ``~/.nexuscli/models.json`` (the same BYOK store
    the Custom tab reads); activation switches to the first picked model so
    "勾选并添加" feels like the screenshot's 添加所选 + immediate use. Returns
    True when the selector loop should exit (activation happened or nothing
    was addable).
    """
    profiles: list[ModelProfile] = []
    for item in listings:
        base_url = item.base_url or (config.llm.base_url or "")
        if not base_url:
            console.print(f"[red]跳过 {item.model}：没有可用的 base_url。[/red]")
            continue
        try:
            profile = ModelProfile.custom_profile(
                name=f"API · {item.model}",
                provider="openai-compatible",
                model=item.model,
                base_url=base_url,
                context_window=item.context_window,
                api_key=config.llm.api_key,
                api_key_env="NEXUSCLI_API_KEY",
            )
        except ValueError as exc:
            console.print(f"[red]跳过 {item.model}：[/red]{exc}")
            continue
        capability_note = _capability_description(item)
        if capability_note:
            profile = dataclasses.replace(profile, description=capability_note)
        profiles.append(profile)
    if not profiles:
        return False
    added = 0
    for profile in profiles:
        existing = store.list()
        duplicate = any(
            p.model == profile.model and p.base_url == profile.base_url for p in existing
        )
        if not duplicate:
            store.add(profile)
            added += 1
    console.print(f"[green]已添加 {added} 个模型到 Custom 列表[/green] [dim]({store.path})[/dim]")
    _activate_model(profiles[0], config, agent, registry, renderer, cwd)
    console.print(f"[green]已切换到：[/green]{profiles[0].model}")
    return True


def _capability_description(listing) -> str:
    """One-line capability note persisted into the profile description.

    Mirrors the selector badges: effort support maps to the /effort levels,
    disable-ability is noted so users know they can turn thinking off; an
    undeclared capability (None) contributes nothing.
    """
    parts: list[str] = []
    if listing.reasoning_effort_supported:
        parts.append("支持强度档位 (/effort minimal/low/medium/high)")
    if listing.thinking_can_disable:
        parts.append("思考可关闭")
    return "；".join(parts)


def _prompt_custom_model(console: Console) -> ModelProfile | None:
    console.print("\n[bold]Add custom model[/bold]")
    provider = Prompt.ask(
        "Provider",
        choices=list(PROVIDER_DEFAULTS),
        default="openai-compatible",
    )
    provider_label, default_base_url, default_context = _provider_defaults(provider)
    model = Prompt.ask("Model ID").strip()
    if not model:
        console.print("[red]Model ID is required.[/red]")
        return None
    name = Prompt.ask("Display name", default=f"{provider_label} · {model}").strip()
    base_url = Prompt.ask("Base URL", default=default_base_url).strip()
    api_key_env = Prompt.ask(
        "API key environment variable",
        default=_provider_api_key_env(provider),
    ).strip()
    api_key = Prompt.ask(
        f"API key (optional; leave blank to use ${api_key_env})",
        default="",
        password=True,
        show_default=False,
    )
    context_text = Prompt.ask("Context window", default=str(default_context)).replace(",", "")
    try:
        context_window = int(context_text)
        return ModelProfile.custom_profile(
            name=name,
            provider=provider,
            model=model,
            base_url=base_url,
            context_window=context_window,
            api_key=api_key,
            api_key_env=api_key_env,
        )
    except ValueError as exc:
        console.print(f"[red]Invalid custom model:[/red] {exc}")
        return None


def _activate_model(
    profile: ModelProfile,
    config: NexusCliConfig,
    agent: Agent,
    registry: ToolRegistry,
    renderer: RichRenderer,
    cwd: str,
) -> None:
    old_provider = config.llm.provider.lower()
    old_api_key = config.llm.api_key
    config.llm.provider = profile.provider
    config.llm.model = profile.model
    config.llm.base_url = profile.base_url
    config.llm.context_window = profile.context_window
    config.llm.api_key = profile.resolve_api_key(
        current_provider=old_provider,
        current_api_key=old_api_key,
    )
    if not config.llm.api_key:
        # Name the variable this provider actually reads. The generic
        # NEXUSCLI_API_KEY hint was wrong for every provider that has its own
        # env var, so the user set the variable the message named and the
        # switch kept failing.
        candidates = list(PROVIDER_API_KEY_ENVS.get(profile.provider.lower(), ()))
        candidates.append(profile.api_key_env or _provider_api_key_env(profile.provider))
        candidates.append("NEXUSCLI_API_KEY")
        names = ", ".join(dict.fromkeys(name for name in candidates if name))
        renderer.console.print(
            f"[yellow]Switched to {profile.model}, but no API key is configured for "
            f"provider {profile.provider}. Set one of: {names}[/yellow]"
        )
    client = create_llm_client(config.llm, telemetry_enabled=config.telemetry.enabled)
    agent.llm_client = client
    agent.system_prompt = PromptAssembler(
        config=config,
        cwd=cwd,
        tool_names=registry.list_names(),
        model=client.model_name,
        provider=client.provider_name,
    ).build_static()
    renderer.set_context_window(client.max_context_window)


def _provider_defaults(provider: str) -> tuple[str, str, int]:
    normalized = provider.lower()
    if normalized in PROVIDER_DEFAULTS:
        return PROVIDER_DEFAULTS[normalized]
    return (provider, config_base_url(provider), 128_000)


def config_base_url(provider: str) -> str:
    from nexuscli.llm.factory import DEEPSEEK_BASE_URL, OPENAI_BASE_URL, PROVIDER_BASE_URLS

    normalized = provider.lower()
    if normalized == "deepseek":
        return DEEPSEEK_BASE_URL
    return PROVIDER_BASE_URLS.get(normalized, OPENAI_BASE_URL)


def _provider_api_key_env(provider: str) -> str:
    return {
        "deepseek": "DEEPSEEK_API_KEY",
        "glm": "ZAI_API_KEY",
        "zhipu": "ZAI_API_KEY",
        "openai": "OPENAI_API_KEY",
        "openai-compatible": "NEXUSCLI_API_KEY",
    }.get(provider.lower(), "NEXUSCLI_API_KEY")
