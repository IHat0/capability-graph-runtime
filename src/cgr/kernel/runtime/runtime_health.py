"""Health snapshot models for the Capability Graph Runtime."""

from pydantic import BaseModel, ConfigDict

from cgr.kernel.contracts import HealthStatus, PluginState


class PluginHealthSnapshot(BaseModel):
    """Immutable health information for a registered plugin."""

    model_config = ConfigDict(frozen=True)

    plugin_id: str
    plugin_name: str
    plugin_version: str
    state: PluginState
    health: HealthStatus
    capabilities: list[str]


class RuntimeHealthSnapshot(BaseModel):
    """Immutable health information for the runtime and its plugins."""

    model_config = ConfigDict(frozen=True)

    healthy: bool
    plugin_count: int
    plugins: list[PluginHealthSnapshot]
