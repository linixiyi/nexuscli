"""plugins/loader.py — Local-directory plugin discovery (minimal MVP).

A plugin is a directory under ``<cwd>/.nexuscli/plugins/<name>/`` holding a
``plugin.json`` manifest plus up to three component subdirectories: ``agents/``
(markdown subagent definitions), ``skills/`` (``<name>/SKILL.md`` packs), and
``commands/`` (markdown slash commands). This module only *discovers* plugins;
merging the three component kinds into the existing registries happens in the
consumer functions (:func:`nexuscli.agent.subagent.load_subagents`,
``SkillRegistry._load_all``, :func:`nexuscli.entrypoints.slash_commands.
load_slash_commands`). Pure file reads — no network, no execution.

Scope cuts (deliberate; mirrors the ZCode reference implementation at
``apps/zcode-cli`` without porting its TS surface): local project-level
directories only — no user-level plugin directory and no remote/marketplace
distribution (github archives, zips); no explicit component-path declarations
in the manifest (ZCode ``collectComponentDirs`` string/array forms); no
``${plugin}:${component}`` namespace isolation — plugin components keep the
existing "last scan wins" override semantics; hooks / MCP servers /
lspServers / outputStyles component types, userConfig options, and diagnostic
structures (PluginDiagnostic) are all out of scope. nexusCLI takes the
smallest slice: local directory + three default subdirectories + three
prompt-style component kinds.

Robustness contract: a missing or broken manifest, or a bad required field,
warns through :func:`warnings.warn` with a ``[nexuscli:plugins]`` prefix and
skips that plugin only — startup must never be taken down by one bad plugin.
Optional fields (``description``/``version``) are lenient: a non-string value
falls back to the default instead of rejecting the plugin. Unknown manifest
keys are silently ignored.

Windows compatibility: every path is built with :mod:`pathlib` and matched
with ``Path.glob()`` — never string concatenation with path separators (the
same convention as the other asset loaders, e.g. ``agent/subagent.py``).
"""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "Plugin",
    "PluginManifest",
    "load_plugins",
    "plugin_root",
    "reset_plugins_cache",
]

# Module-level cache keyed by the resolved cwd. ``Path.resolve()`` normalizes
# Windows drive-letter case and ``/`` vs ``\`` separator differences, so the
# same directory reached through different spellings shares one cache entry.
# A cache hit returns the very same list object and never re-emits warnings,
# so a bad manifest warns exactly once per process (startup assembly warms
# the cache; later consumers stay silent).
_CACHE: dict[str, list[Plugin]] = {}


@dataclass(slots=True)
class PluginManifest:
    """Parsed ``plugin.json``: ``name`` required, the rest optional."""

    name: str
    description: str = ""
    version: str = ""


@dataclass(slots=True)
class Plugin:
    """One discovered plugin: identity plus its present component directories.

    A component directory field is set only when the subdirectory exists and
    is a directory (ZCode ``collectComponentDirs``' ``directoryExists``
    semantics); otherwise it stays ``None`` and consumers skip it.
    """

    name: str
    root: Path
    manifest: PluginManifest
    agents_dir: Path | None
    skills_dir: Path | None
    commands_dir: Path | None


def _warn(message: str) -> None:
    warnings.warn(f"[nexuscli:plugins] {message}", stacklevel=3)


def plugin_root(cwd: str) -> Path:
    """Project-level plugin directory for *cwd*."""
    return Path(cwd) / ".nexuscli" / "plugins"


def reset_plugins_cache() -> None:
    """Drop the discovery cache (test hook; the next load re-scans and re-warns)."""
    _CACHE.clear()


def _component_dir(root: Path, name: str) -> Path | None:
    candidate = root / name
    return candidate if candidate.is_dir() else None


def _parse_manifest(directory: Path) -> PluginManifest | None:
    """Parse ``<directory>/plugin.json``; ``None`` means "warned and skipped"."""
    manifest_path = directory / "plugin.json"
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # UnicodeDecodeError is a ValueError, not an OSError — a plugin.json
        # with invalid bytes must warn+skip, never escape.
        _warn(f"plugin {directory.name}: plugin.json unreadable ({exc}), skipped")
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        _warn(f"plugin {directory.name}: plugin.json is not valid JSON ({exc}), skipped")
        return None
    if not isinstance(data, dict):
        _warn(f"plugin {directory.name}: plugin.json must be a JSON object, skipped")
        return None
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        _warn(f"plugin {directory.name}: plugin.json needs a non-empty string 'name', skipped")
        return None
    # Optional fields are lenient: a non-string value falls back to the
    # default instead of rejecting the whole plugin. Unknown keys are
    # silently ignored (ZCode PluginManifest contract: only ``name`` is
    # required).
    description = data.get("description")
    version = data.get("version")
    return PluginManifest(
        name=name.strip(),
        description=description if isinstance(description, str) else "",
        version=version if isinstance(version, str) else "",
    )


def _load_plugin(directory: Path) -> Plugin | None:
    manifest = _parse_manifest(directory)
    if manifest is None:
        return None
    return Plugin(
        name=manifest.name,
        root=directory,
        manifest=manifest,
        agents_dir=_component_dir(directory, "agents"),
        skills_dir=_component_dir(directory, "skills"),
        commands_dir=_component_dir(directory, "commands"),
    )


def load_plugins(cwd: str) -> list[Plugin]:
    """Discover plugins under *cwd*'s project plugin directory.

    The first call enumerates ``.nexuscli/plugins/`` subdirectories in name
    order and parses each manifest; later calls with the same resolved cwd
    return the same list object without re-scanning or re-warning. Any single
    plugin failure only skips that plugin — callers always get a list (empty
    when the plugin directory does not exist), never an exception.
    """
    cache_key = str(Path(cwd).resolve())
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached
    root = plugin_root(cwd)
    plugins: list[Plugin] = []
    if root.is_dir():
        for directory in sorted(path for path in root.iterdir() if path.is_dir()):
            plugin = _load_plugin(directory)
            if plugin is not None:
                plugins.append(plugin)
    _CACHE[cache_key] = plugins
    return plugins
