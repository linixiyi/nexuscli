"""Local-directory plugin support (project + user level).

See ``loader.py`` for the discovery contract (manifest-only reads, no code
execution), the ``<plugin>.<component>`` namespace, the tool-component
registration path, and the broken-manifest warn-and-skip guarantee.
"""

from nexuscli.plugins.loader import (
    Plugin,
    PluginManifest,
    PluginToolDecl,
    component_ref,
    load_plugins,
    plugin_root,
    register_plugin_tools,
    reset_plugins_cache,
    user_plugin_root,
)

__all__ = [
    "Plugin",
    "PluginManifest",
    "PluginToolDecl",
    "component_ref",
    "load_plugins",
    "plugin_root",
    "register_plugin_tools",
    "reset_plugins_cache",
    "user_plugin_root",
]
