"""Local-directory plugin support (project-level MVP).

See ``loader.py`` for the discovery contract, scope cuts, and the
broken-manifest warn-and-skip guarantee.
"""

from nexuscli.plugins.loader import (
    Plugin,
    PluginManifest,
    load_plugins,
    plugin_root,
    reset_plugins_cache,
)

__all__ = [
    "Plugin",
    "PluginManifest",
    "load_plugins",
    "plugin_root",
    "reset_plugins_cache",
]
