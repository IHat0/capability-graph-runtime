"""Product Phase 5.2 RDKit capability and artifact regressions."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from cgr.kernel.contracts import (
    CapabilityVersion,
    ExecutionContext,
    ExecutionStatus,
    HealthStatus,
)
from cgr.molecular.cheminformatics import (
    MolecularConformerSet,
    MolecularFragmentSet,
    MolecularGeometryValidation,
    MolecularGraph,
    MolecularScaffold,
)
from cgr.molecular.rdkit_adapter import (
    CONFORMER_GENERATION,
    FRAGMENT,
    GEOMETRY_VALIDATION,
    PREPARE,
    SCAFFOLD,
    SMILES_PARSE,
    STRUCTURE_INGESTION,
    RDKitCheminformaticsAdapter,
)
from cgr.science import (
    ArtifactPointer,
    ArtifactReference,
    CapabilityInvocation,
    CreationProvenance,
)

VERSION = CapabilityVersion(major=1, minor=0, patch=0)
EXPERIMENT = ArtifactPointer(
    artifact_identifier="experiment.phase5-2",
    content_sha256="e" * 64,
)
FIXTURES = Path(__file__).parent / "fixtures" / "molecular-ingestion"


class MemoryPayloadStore:
    """Exact content-addressed payload store used only by adapter tests."""

    def __init__(self) -> None:
        self.payloads: dict[tuple[str, str], bytes] = {}

    def read(self, reference: ArtifactReference) -> bytes:
        return self.payloads[(reference.artifact_identifier, reference.content_sha256)]

    def write(self, reference: ArtifactReference, payload: bytes) -> None:
        assert hashlib.sha256(payload).hexdigest() == reference.content_sha256
        assert reference.byte_size == len(payload)
        key = (reference.artifact_identifier, reference.content_sha256)
        existing = self.payloads.get(key)
        assert existing is None or existing == payload
        self.payloads[key] = payload


def _source_reference(
    identifier: str,
    artifact_type: str,
    payload: bytes,
    media_type: str,
) -> ArtifactReference:
    return ArtifactReference(
        artifact_identifier=identifier,
        schema_version=VERSION,
        artifact_type=artifact_type,
        media_type=media_type,
        content_sha256=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload),
        provenance=CreationProvenance(
            producer="test.fixture",
            producer_version=VERSION,
        ),
    )


def _invocation(
    adapter: RDKitCheminformaticsAdapter,
    capability_name: str,
    *,
    inputs: tuple[ArtifactReference, ...] = (),
    parameters: dict[str, object] | None = None,
    execution_identifier: str = "execution.phase5-2",
) -> CapabilityInvocation:
    envelope = next(
        envelope
        for envelope in adapter.declaration.capabilities
        if envelope.descriptor.capability_name == capability_name
    )
    return CapabilityInvocation(
        capability=envelope.descriptor,
        input_artifacts=inputs,
        experiment=EXPERIMENT,
        context=ExecutionContext(execution_id=execution_identifier),
        parameters=parameters or {},
    )


def _execute_smiles(
    adapter: RDKitCheminformaticsAdapter,
    smiles: str,
) -> ArtifactReference:
    result = adapter.invoke(
        _invocation(
            adapter,
            SMILES_PARSE,
            parameters={"smiles": smiles},
        )
    )
    assert result.status is ExecutionStatus.SUCCESS
    assert result.failure is None
    return result.output_artifacts[0]


def test_declaration_exposes_all_phase5_2_capabilities_without_importing_rdkit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "rdkit", None)
    store = MemoryPayloadStore()
    adapter = RDKitCheminformaticsAdapter(store)

    assert tuple(
        envelope.descriptor.capability_name
        for envelope in adapter.declaration.capabilities
    ) == (
        CONFORMER_GENERATION,
        FRAGMENT,
        GEOMETRY_VALIDATION,
        PREPARE,
        SCAFFOLD,
        SMILES_PARSE,
        STRUCTURE_INGESTION,
    )
    assert adapter.declaration.engine.engine_identifier == "engine.rdkit"
    assert adapter.health().status is HealthStatus.UNAVAILABLE


def test_smiles_parse_creates_generic_graph_and_preserves_atom_order() -> None:
    pytest.importorskip("rdkit")
    store = MemoryPayloadStore()
    adapter = RDKitCheminformaticsAdapter(store)

    artifact = _execute_smiles(adapter, "CCO")
    payload = store.read(artifact)
    graph = MolecularGraph.model_validate_json(payload)

    assert artifact.artifact_type == "molecular_graph"
    assert graph.canonical_smiles == "CCO"
    assert [atom.element_symbol for atom in graph.atoms] == ["C", "C", "O"]
    assert [atom.atom_index for atom in graph.atoms] == [0, 1, 2]
    assert [atom.source_atom_index for atom in graph.atoms] == [0, 1, 2]
    assert graph.formal_charge == 0
    assert "rdkit" not in payload.decode("utf-8").lower()


def test_invalid_smiles_returns_controlled_failure() -> None:
    pytest.importorskip("rdkit")
    adapter = RDKitCheminformaticsAdapter(MemoryPayloadStore())

    result = adapter.invoke(
        _invocation(
            adapter,
            SMILES_PARSE,
            parameters={"smiles": "not valid smiles("},
        )
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.failure is not None
    assert result.failure.code == "molecular_input_invalid"
    assert "not valid smiles" not in result.failure.message


def test_preparation_adds_explicit_hydrogens_and_preserves_formal_charge() -> None:
    pytest.importorskip("rdkit")
    store = MemoryPayloadStore()
    adapter = RDKitCheminformaticsAdapter(store)
    source = _execute_smiles(adapter, "[NH4+]")

    result = adapter.invoke(_invocation(adapter, PREPARE, inputs=(source,)))
    prepared = MolecularGraph.model_validate_json(
        store.read(result.output_artifacts[0])
    )

    assert result.status is ExecutionStatus.SUCCESS
    assert prepared.explicit_hydrogens is True
    assert prepared.formal_charge == 1
    assert len(prepared.atoms) == 5
    assert prepared.atoms[0].source_atom_index == 0
    assert all(atom.source_atom_index is None for atom in prepared.atoms[1:])
    assert result.lineage[0].source == source.pointer


def test_seeded_conformer_generation_is_byte_deterministic() -> None:
    pytest.importorskip("rdkit")
    store = MemoryPayloadStore()
    adapter = RDKitCheminformaticsAdapter(store)
    graph = _execute_smiles(adapter, "CCO")
    prepared_result = adapter.invoke(_invocation(adapter, PREPARE, inputs=(graph,)))
    prepared = prepared_result.output_artifacts[0]
    parameters = {
        "conformer_count": 3,
        "random_seed": 17,
        "maximum_iterations": 200,
    }

    first = adapter.invoke(
        _invocation(
            adapter,
            CONFORMER_GENERATION,
            inputs=(prepared,),
            parameters=parameters,
            execution_identifier="execution.conformer-first",
        )
    )
    second = adapter.invoke(
        _invocation(
            adapter,
            CONFORMER_GENERATION,
            inputs=(prepared,),
            parameters=parameters,
            execution_identifier="execution.conformer-second",
        )
    )

    assert first.status is ExecutionStatus.SUCCESS
    assert second.status is ExecutionStatus.SUCCESS
    assert (
        first.output_artifacts[0].content_sha256
        == second.output_artifacts[0].content_sha256
    )
    first_payload = store.read(first.output_artifacts[0])
    second_payload = store.read(second.output_artifacts[0])
    assert first_payload == second_payload
    conformers = MolecularConformerSet.model_validate_json(first_payload)
    assert conformers.random_seed == 17
    assert 1 <= len(conformers.conformers) <= 3
    assert all(
        len(conformer.coordinates) == len(conformers.source_graph.atoms)
        for conformer in conformers.conformers
    )


def test_generated_geometry_passes_bounded_validation() -> None:
    pytest.importorskip("rdkit")
    store = MemoryPayloadStore()
    adapter = RDKitCheminformaticsAdapter(store)
    graph = _execute_smiles(adapter, "CCO")
    prepared = adapter.invoke(
        _invocation(adapter, PREPARE, inputs=(graph,))
    ).output_artifacts[0]
    conformers = adapter.invoke(
        _invocation(
            adapter,
            CONFORMER_GENERATION,
            inputs=(prepared,),
            parameters={"conformer_count": 2, "random_seed": 23},
        )
    ).output_artifacts[0]

    result = adapter.invoke(
        _invocation(adapter, GEOMETRY_VALIDATION, inputs=(conformers,))
    )
    validation = MolecularGeometryValidation.model_validate_json(
        store.read(result.output_artifacts[0])
    )

    assert result.status is ExecutionStatus.SUCCESS
    assert validation.valid is True
    assert validation.findings == ()
    assert validation.evaluated_conformer_count >= 1


def test_fragment_and_scaffold_operations_return_generic_artifacts() -> None:
    pytest.importorskip("rdkit")
    store = MemoryPayloadStore()
    adapter = RDKitCheminformaticsAdapter(store)

    disconnected = _execute_smiles(adapter, "CC.O")
    fragment_result = adapter.invoke(
        _invocation(adapter, FRAGMENT, inputs=(disconnected,))
    )
    fragments = MolecularFragmentSet.model_validate_json(
        store.read(fragment_result.output_artifacts[0])
    )

    aromatic = _execute_smiles(adapter, "c1ccccc1CC")
    scaffold_result = adapter.invoke(_invocation(adapter, SCAFFOLD, inputs=(aromatic,)))
    scaffold = MolecularScaffold.model_validate_json(
        store.read(scaffold_result.output_artifacts[0])
    )

    assert fragment_result.status is ExecutionStatus.SUCCESS
    assert [fragment.canonical_smiles for fragment in fragments.fragments] == [
        "CC",
        "O",
    ]
    assert scaffold_result.status is ExecutionStatus.SUCCESS
    assert scaffold.canonical_smiles == "c1ccccc1"
    assert scaffold.source_atom_indices == (0, 1, 2, 3, 4, 5)


def test_existing_structure_ingestion_is_exposed_through_rdkit_adapter() -> None:
    pytest.importorskip("rdkit")
    store = MemoryPayloadStore()
    adapter = RDKitCheminformaticsAdapter(store)
    source_bytes = (FIXTURES / "minimal.mol").read_bytes()
    source = _source_reference(
        "molecular-structure.phase5-2",
        "molecular_structure",
        source_bytes,
        "chemical/x-mdl-molfile",
    )
    store.write(source, source_bytes)

    result = adapter.invoke(
        _invocation(
            adapter,
            STRUCTURE_INGESTION,
            inputs=(source,),
            parameters={
                "structure_format": "mol",
                "coordinate_unit": "angstrom",
                "structure_identifier": "structure.phase5-2",
                "system_identifier": "system.phase5-2",
            },
        )
    )

    assert result.status is ExecutionStatus.SUCCESS
    assert result.output_artifacts[0].artifact_type == "molecular_topology"
    topology_payload = json.loads(store.read(result.output_artifacts[0]))
    assert topology_payload["parser_identifier"] == "rdkit"
    assert len(topology_payload["atoms"]) == 2
    assert result.lineage[0].source == source.pointer
