"""
Plugin Registry for the Capability Graph Runtime.

The PluginRegistry is responsible for registering, unregistering,
discovering, and resolving plugins by capability.

The registry does not execute plugins.
It only manages them.
"""

from __future__ import annotations

from typing import Any

from cgr.kernel.contracts.capability import Capability
from cgr.kernel.contracts.plugin import Plugin


class PluginRegistry:
    """
    Registry of loaded plugins.
    """

    def __init__(self) -> None:
        self._plugins: dict[str, Plugin[Any, Any]] = {}

    def register(self, plugin: Plugin[Any, Any]) -> None:
        """
        Register a plugin.

        Raises
        ------
        ValueError
            If a plugin with the same id is already registered.
        """
        plugin_id = plugin.metadata.id

        if plugin_id in self._plugins:
            raise ValueError(
                f"Plugin '{plugin_id}' is already registered."
            )

        self._plugins[plugin_id] = plugin

    def unregister(self, plugin_id: str) -> None:
        """
        Remove a plugin from the registry.
        """
        self._plugins.pop(plugin_id, None)

    def get(self, plugin_id: str) -> Plugin[Any, Any]:
        """
        Return a plugin by id.

        Raises
        ------
        KeyError
            If the plugin is not registered.
        """
        return self._plugins[plugin_id]

    def all(self) -> list[Plugin[Any, Any]]:
        """
        Return all registered plugins.
        """
        return list(self._plugins.values())

    def find_by_capability(
        self,
        capability: Capability,
    ) -> list[Plugin[Any, Any]]:
        """
        Return every plugin implementing a capability.
        """
        matches: list[Plugin[Any, Any]] = []

        for plugin in self._plugins.values():
            if plugin.metadata.supports(capability.id):
                matches.append(plugin)

        return matches

    def __len__(self) -> int:
        return len(self._plugins)

    def __contains__(self, plugin_id: str) -> bool:
        return plugin_id in self._plugins