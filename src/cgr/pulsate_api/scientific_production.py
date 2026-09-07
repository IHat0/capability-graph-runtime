"""Production composition for the complete Pulsate scientific runtime.

This module is the single seam between the enterprise application boundary and
the Phase 1-8 scientific implementation.  It deliberately reuses the existing
immutable artifact repository, native adapters, capability plans, and workflow
runtime instead of introducing another execution stack.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from cgr.electronic_structure import PySCFElectronicStructureAdapter
from cgr.kernel.contracts import HealthStatus
from cgr.molecular import (
    MeekoDockingPreparationAdapter,
    MolecularArtifactRepository,
    RDKitCheminformaticsAdapter,
)
from cgr.protein_design import (
    ExternalProteinEngineAdapter,
    configured_protein_design_adapters,
)
from cgr.quantum_workflow import QiskitQuantumWorkflowAdapter
from cgr.science import ArtifactReference

from .natural_language import NaturalLanguageModelProvider
from .phase8_scientific_handlers import (
    aqueous_conformer_registry,
    bond_dissociation_registry,
    covalent_transition_state_registry,
    metal_active_site_registry,
    molecular_ground_state_registry,
    molecular_ground_state_sweep_registry,
    protein_design_registry,
    protein_ligand_discovery_registry,
    structure_analysis_registry,
)
from .scientific_capability_catalogue import phase8_scientific_capability_catalogue
from .scientific_capability_truth import (
    ScientificCapabilityImplementation,
    ScientificCapabilityTruthCatalogue,
)
from .scientific_compute_routing import ScientificComputeRouter
from .scientific_executions import ScientificExecutionRepository
from .scientific_runtime import (
    ScientificCapabilityFailure,
    ScientificCapabilityOutcome,
    ScientificObjectiveRuntime,
    ScientistCapabilityHandler,
    ScientistCapabilityRegistry,
    ScientistResultAssembler,
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
        handlers: Mapping[str, ScientistCapabilityHandler],
    ) -> None:
        self.capability_name = capability_name
        self.handlers = dict(handlers)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        profile = (
            objective.research_requirements.capability_profile
            if objective.research_requirements is not None
            else objective.task_type
        )
        handler = self.handlers.get(profile)  # type: ignore[arg-type]
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
    protein_adapters: tuple[ExternalProteinEngineAdapter, ...] | None = None,
) -> ScientistCapabilityRegistry:
    """Compose all production scientific vertical slices into one routed registry."""

    rdkit = RDKitCheminformaticsAdapter(payload_store)
    pyscf = PySCFElectronicStructureAdapter(
        payload_store,
        private_state_store=private_state_store,
    )
    qiskit = QiskitQuantumWorkflowAdapter(payload_store)
    meeko = MeekoDockingPreparationAdapter(payload_store)
    protein_adapters = (
        configured_protein_design_adapters(payload_store)
        if protein_adapters is None
        else protein_adapters
    )
    task_registries: dict[str, ScientistCapabilityRegistry] = {
        "structure_analysis": structure_analysis_registry(
            store=payload_store,
        ),
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
        "molecular_ground_state_vqe": molecular_ground_state_registry(
            store=payload_store,
            pyscf_adapter=pyscf,
            qiskit_adapter=qiskit,
        ),        "molecular_ground_state_vqe_sweep": molecular_ground_state_sweep_registry(
            store=payload_store,
            pyscf_adapter=pyscf,
            qiskit_adapter=qiskit,
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
        "de_novo_protein_design": protein_design_registry(
            store=payload_store,
            adapters=protein_adapters,
        ),
    }
    routed: dict[str, dict[str, ScientistCapabilityHandler]] = {}
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
    language_model_provider: NaturalLanguageModelProvider | None = None,
    quantum_capability_provider: Callable[[], Mapping[str, object]] | None = None,
) -> ScientificProductionComposition:
    """Build, but do not start, the production Phase 1-8 composition."""

    payload_store = DurableScientificPayloadStore(
        application_data_root / "scientific-artifacts"
    )
    private_state_store = DurableScientificPayloadStore(
        application_data_root / "scientific-private-state"
    )
    protein_adapters = configured_protein_design_adapters(payload_store)
    registry = phase8_production_registry(
        payload_store=payload_store,
        private_state_store=private_state_store,
        protein_adapters=protein_adapters,
    )
    result_assembler = ScientistResultAssembler(
        payload_store,
        language_model_provider,
    )
    registry.register("scientist.result_assemble", result_assembler)
    capability_truth = ScientificCapabilityTruthCatalogue.from_runtime(
        catalogue=phase8_scientific_capability_catalogue(),
        registered_capability_names=registry.identities(),
        implementation_overrides={
            envelope.descriptor.capability_name: (
                ScientificCapabilityImplementation.EXTERNAL_EXECUTOR
            )
            for adapter in protein_adapters
            for envelope in adapter.declaration.capabilities
        },
        configured_external_capability_names=tuple(
            envelope.descriptor.capability_name
            for adapter in protein_adapters
            if adapter.health().status is HealthStatus.HEALTHY
            for envelope in adapter.declaration.capabilities
        ),
    )
    healthy_protein_adapters = tuple(
        adapter
        for adapter in protein_adapters
        if adapter.health().status is HealthStatus.HEALTHY
    )
    capability_execution_targets: dict[str, tuple[str, ...]] = {}
    for adapter in healthy_protein_adapters:
        for envelope in adapter.declaration.capabilities:
            capability_execution_targets[
                envelope.descriptor.capability_name
            ] = envelope.execution_targets
    compute_router = ScientificComputeRouter(
        capability_execution_targets=capability_execution_targets,
        quantum_capability_provider=quantum_capability_provider,
        execution_environment="pulsate_production",
    )
    execution_repository.bind_capability_truth(capability_truth)
    execution_repository.bind_compute_router(compute_router)
    runtime = ScientificObjectiveRuntime(
        root=application_data_root / "scientific-workflows",
        execution_repository=execution_repository,
        capability_registry=registry,
        result_assembler=result_assembler,
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
