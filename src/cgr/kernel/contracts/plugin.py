"""
Plugin contract for the Capability Graph Runtime.

Every plugin in the runtime must implement this interface.

The kernel never knows *what* a plugin is. It only knows that it
implements this contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from .execution_request import ExecutionRequest
from .execution_result import ExecutionResult
from .health_status import HealthStatus
from .plugin_metadata import PluginMetadata
from .plugin_state import PluginState

TInput = TypeVar("TInput")
TOutput = TypeVar("TOutput")


class Plugin(ABC, Generic[TInput, TOutput]):
    """
    Abstract base class for all CGR plugins.

    Plugins provide one or more capabilities to the runtime.
    """

    @property
    @abstractmethod
    def metadata(self) -> PluginMetadata:
        """
        Return immutable metadata describing this plugin.
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def state(self) -> PluginState:
        """
        Return the current lifecycle state.
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def health(self) -> HealthStatus:
        """
        Return the current health status.
        """
        raise NotImplementedError

    @abstractmethod
    def initialize(self) -> None:
        """
        Initialize the plugin.

        Called once after the plugin is loaded by the runtime.
        """
        raise NotImplementedError

    @abstractmethod
    def shutdown(self) -> None:
        """
        Shutdown the plugin.

        Called before the runtime unloads the plugin.
        """
        raise NotImplementedError

    @abstractmethod
    def execute(
        self,
        request: ExecutionRequest[TInput],
    ) -> ExecutionResult[TOutput]:
        """
        Execute a capability request.

        Parameters
        ----------
        request
            The execution request.

        Returns
        -------
        ExecutionResult
            The result of the execution.
        """
        raise NotImplementedError