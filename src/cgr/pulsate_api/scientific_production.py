"""Production composition for the complete Pulsate scientific runtime.

This module is the single seam between the enterprise application boundary and
the Phase 1-8 scientific implementation.  It deliberately reuses the existing
immutable artifact repository, native adapters, capability plans, and workflow
runtime instead of introducing another execution stack.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from cgr.electronic_structure import PySCFElectronicStructureAdapter
from cgr.molecular import (
    MeekoDockingPreparationAdapter,
    MolecularArtifactRepository,
    RDKitCheminformaticsAdapter,
)
from cgr.quantum_workflow import QiskitQuantumWorkflowAdapter
from cgr.science import ArtifactReference

from .phase8_scientific_handlers import (
    aqueous_conformer_registry,
    bond_dissociation_registry,
    covalent_transition_state_registry,
    metal_active_site_registry,
    protein_ligand_discovery_registry,
)
from .scientific_executions import ScientificExecutionRepository
from .scientific_objectives import ScientificTask
from .scientific_runtime import (
    ScientificCapabilityFailure,
    ScientificCapabilityOutcome,
    ScientificObjectiveRuntime,
    ScientistCapabilityHandler,
    ScientistCapabilityRegistry,
)


class DurableScientificPayloadStore:
    """Exact content-addressed bytes backed by the enterprise artifact store."""

    def __init__(self, root: Path) -> None:
        self.repository = MolecularArtifactRepository(root)

    def start(self) -> None:
        self.repository.start()

    def close(self) -> None:
        self.repository.close()

    @property
    def _started(self) -> bool:
        """Expose lifecycle state to the existing production readiness check."""

        return bool(self.repository._started)

    def read(self, reference: ArtifactReference) -> bytes:
        return self.repository.read(reference)

    def write(self, reference: ArtifactReference, payload: bytes) -> None:
        self.repository.put(reference, payload)


class _TaskRoutedHandler:
    """Select a same-named capability by the validated scientific task."""

    def __init__(
        self,
        capability_name: str,
        handlers: Mapping[ScientificTask, ScientistCapabilityHandler],
    ) -> None:
        self.capability_name = capability_name
        self.handlers = dict(handlers)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        handler = self.handlers.get(objective.task_type)
        if handler is None:
            raise ScientificCapabilityFailure(
                "scientific_capability_unavailable",
                f"Capability {self.capability_name} is unavailable for this scientific task.",
            )
        return handler.execute(
            invocation=invocation,
            objective=objective,
            record=record,
        )


def phase8_production_registry(
    *,
    payload_store: DurableScientificPayloadStore,
    private_state_store: DurableScientificPayloadStore,
) -> ScientistCapabilityRegistry:
    """Compose all five Phase 8 vertical slices into one task-routed registry."""

    rdkit = RDKitCheminformaticsAdapter(payload_store)
    pyscf = PySCFElectronicStructureAdapter(
        payload_store,
        private_state_store=private_state_store,
    )
    qiskit = QiskitQuantumWorkflowAdapter(payload_store)
    meeko = MeekoDockingPreparationAdapter(payload_store)
    task_registries: dict[ScientificTask, ScientistCapabilityRegistry] = {
        "solvated_conformer_comparison": aqueous_conformer_registry(
            store=payload_store,
            rdkit_adapter=rdkit,
            pyscf_adapter=pyscf,
        ),
        "bond_dissociation_scan": bond_dissociation_registry(
            store=payload_store,
            rdkit_adapter=rdkit,
            pyscf_adapter=pyscf,
        ),
        "metal_active_site_quantum": metal_active_site_registry(
            store=payload_store,
            pyscf_adapter=pyscf,
            qiskit_adapter=qiskit,
        ),
        "covalent_transition_state": covalent_transition_state_registry(
            store=payload_store,
            private_store=private_state_store,
            pyscf_adapter=pyscf,
        ),
        "protein_ligand_discovery": protein_ligand_discovery_registry(
            store=payload_store,
            meeko_adapter=meeko,
        ),
    }
    routed: dict[str, dict[ScientificTask, ScientistCapabilityHandler]] = {}
    for task_type, registry in task_registries.items():
        for capability_name in registry.identities():
            handler = registry.get(capability_name)
            assert handler is not None
            routed.setdefault(capability_name, {})[task_type] = handler
    return ScientistCapabilityRegistry(
        {
            name: _TaskRoutedHandler(name, handlers)
            for name, handlers in sorted(routed.items())
        }
    )


@dataclass(frozen=True, slots=True)
class ScientificProductionComposition:
    """Owned services needed to run science through the production API."""

    payload_store: DurableScientificPayloadStore
    private_state_store: DurableScientificPayloadStore
    runtime: ScientificObjectiveRuntime

    def start(self) -> None:
        self.payload_store.start()
        try:
            self.private_state_store.start()
        except Exception:
            self.payload_store.close()
            raise
        try:
            self.runtime.start()
        except Exception:
            self.private_state_store.close()
            self.payload_store.close()
            raise

    def close(self) -> None:
        try:
            self.runtime.close()
        finally:
            try:
                self.private_state_store.close()
            finally:
                self.payload_store.close()


def create_scientific_production_composition(
    *,
    application_data_root: Path,
    execution_repository: ScientificExecutionRepository,
) -> ScientificProductionComposition:
    """Build, but do not start, the production Phase 1-8 composition."""

    payload_store = DurableScientificPayloadStore(
        application_data_root / "scientific-artifacts"
    )
    private_state_store = DurableScientificPayloadStore(
        application_data_root / "scientific-private-state"
    )
    registry = phase8_production_registry(
        payload_store=payload_store,
        private_state_store=private_state_store,
    )
    runtime = ScientificObjectiveRuntime(
        root=application_data_root / "scientific-workflows",
        execution_repository=execution_repository,
        capability_registry=registry,
    )
    return ScientificProductionComposition(
        payload_store=payload_store,
        private_state_store=private_state_store,
        runtime=runtime,
    )


__all__ = [
    "DurableScientificPayloadStore",
    "ScientificProductionComposition",
    "create_scientific_production_composition",
    "phase8_production_registry",
]
