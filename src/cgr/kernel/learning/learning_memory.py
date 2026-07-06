"""In-memory execution learning and performance statistics."""

from cgr.kernel.contracts import ExecutionStatus
from cgr.shared.events import Event, EventType

from .execution_observation import ExecutionObservation
from .plugin_performance import PluginPerformance


class LearningMemory:
    """Store execution observations and compute plugin performance."""

    def __init__(self) -> None:
        self._observations: list[ExecutionObservation] = []

    def record(self, observation: ExecutionObservation) -> None:
        """Record one execution observation."""
        self._observations.append(observation)

    def observations(self) -> list[ExecutionObservation]:
        """Return a copy of all recorded observations."""
        return list(self._observations)

    def observations_for_capability(
        self,
        capability_id: str,
    ) -> list[ExecutionObservation]:
        """Return observations for one capability."""
        return [
            observation
            for observation in self._observations
            if observation.capability_id == capability_id
        ]

    def performance_for(
        self,
        capability_id: str,
        plugin_id: str,
    ) -> PluginPerformance:
        """Compute aggregate performance for a capability and plugin."""
        observations = [
            observation
            for observation in self._observations
            if observation.capability_id == capability_id
            and observation.plugin_id == plugin_id
        ]
        total = len(observations)
        successful = sum(
            observation.status == ExecutionStatus.SUCCESS
            for observation in observations
        )
        failed = sum(
            observation.status == ExecutionStatus.FAILED
            for observation in observations
        )
        average_duration_ms = (
            sum(observation.duration_ms for observation in observations) / total
            if total
            else 0.0
        )
        success_rate = successful / total if total else 0.0
        return PluginPerformance(
            capability_id=capability_id,
            plugin_id=plugin_id,
            total_executions=total,
            successful_executions=successful,
            failed_executions=failed,
            average_duration_ms=average_duration_ms,
            success_rate=success_rate,
        )

    def rank_plugins(self, capability_id: str) -> list[PluginPerformance]:
        """Rank observed plugins by success, duration, then identifier."""
        plugin_ids = {
            observation.plugin_id
            for observation in self._observations
            if observation.capability_id == capability_id
        }
        performances = [
            self.performance_for(capability_id, plugin_id)
            for plugin_id in plugin_ids
        ]
        return sorted(
            performances,
            key=lambda performance: (
                -performance.success_rate,
                performance.average_duration_ms,
                performance.plugin_id,
            ),
        )

    def consume_event(self, event: Event) -> None:
        """Record an observation from a terminal execution event."""
        if event.type not in {
            EventType.EXECUTION_COMPLETED,
            EventType.EXECUTION_FAILED,
        }:
            return
        if event.execution_id is None:
            return

        payload = event.payload
        status = (
            ExecutionStatus(payload["status"])
            if event.type == EventType.EXECUTION_COMPLETED
            else ExecutionStatus.FAILED
        )
        error_type = payload.get("error_type")
        error_message = payload.get("error_message")
        self.record(
            ExecutionObservation(
                execution_id=event.execution_id,
                capability_id=str(payload["capability_id"]),
                plugin_id=str(payload["plugin_id"]),
                status=status,
                duration_ms=float(payload.get("duration_ms", 0.0)),
                error_type=(
                    str(error_type) if error_type is not None else None
                ),
                error_message=(
                    str(error_message) if error_message is not None else None
                ),
            )
        )
