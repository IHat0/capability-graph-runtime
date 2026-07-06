"""Dynamic plugin loading from Python import paths."""

import importlib
from collections.abc import Iterable
from typing import Any

from cgr.kernel.contracts import Plugin


class PluginLoadError(RuntimeError):
    """Raised when a plugin cannot be loaded from an import path."""


class PluginLoader:
    """Load plugin instances from ``module.path:ClassName`` strings."""

    def load(self, import_path: str) -> Plugin[Any, Any]:
        """Load and instantiate one plugin."""
        if import_path.count(":") != 1:
            raise PluginLoadError(
                "Plugin import path must be in format 'module.path:ClassName'."
            )
        module_path, class_name = import_path.split(":")
        if not module_path or not class_name:
            raise PluginLoadError(
                "Plugin import path must be in format 'module.path:ClassName'."
            )

        try:
            module = importlib.import_module(module_path)
        except Exception as exc:
            raise PluginLoadError(
                f"Could not import plugin module '{module_path}'."
            ) from exc

        try:
            plugin_class = getattr(module, class_name)
        except AttributeError as exc:
            raise PluginLoadError(
                f"Plugin class '{class_name}' was not found in module "
                f"'{module_path}'."
            ) from exc

        try:
            instance = plugin_class()
        except Exception as exc:
            raise PluginLoadError(
                f"Could not instantiate plugin '{import_path}'."
            ) from exc

        if not isinstance(instance, Plugin):
            raise PluginLoadError(
                f"Loaded object '{import_path}' is not a Plugin."
            )
        return instance

    def load_many(
        self,
        import_paths: Iterable[str],
    ) -> list[Plugin[Any, Any]]:
        """Load plugins in the provided order."""
        return [self.load(import_path) for import_path in import_paths]
