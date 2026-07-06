"""Structured capability routing decisions."""

from pydantic import BaseModel, ConfigDict


class RouteDecision(BaseModel):
    """Immutable record of a capability routing decision."""

    model_config = ConfigDict(frozen=True)

    capability_id: str
    selected_plugin_id: str
    candidate_plugin_ids: list[str]
    strategy: str
    reason: str
