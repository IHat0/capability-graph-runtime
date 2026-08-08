"""Phase 8 advanced RDKit geometry foundation tests."""

from __future__ import annotations

import hashlib

import pytest

from cgr.kernel.contracts import (
    CapabilityVersion,
    ExecutionContext,
    ExecutionStatus,
)
from cgr.molecular.advanced_geometry import (
    MolecularDistanceScan,
    MolecularSubstructureResolution,
)
from cgr.molecular.cheminformatics import MolecularGraph
from cgr.molecular.rdkit_adapter import (
    CONSTRAINED_DISTANCE_SCAN,
    PREPARE,
    SMILES_PARSE,
    SUBSTRUCTURE_RESOLUTION,
    RDKitCheminformaticsAdapter,
)
from cgr.molecular.rdkit_advanced_geometry import (
    constrained_bond_distance_scan,
    resolve_smarts_substructure,
)
from cgr.science import (
    ArtifactPointer,
    ArtifactReference,
    CapabilityInvocation,
)


VERSION = CapabilityVersion(major=1, minor=0, patch=0)
EXPERIMENT = ArtifactPointer(
    artifact_identifier="experiment.phase8-advanced-geometry",
    content_sha256="e" * 64,
)


class MemoryPayloadStore:
    def __init__(self) -> None:
        self.payloads: dict[tuple[str, str], bytes] = {}

    def read(self, reference: ArtifactReference) -> bytes:
        return self.payloads[
            (
                reference.artifact_identifier,
                reference.content_sha256,
            )
        ]

    def write(
        self,
        reference: ArtifactReference,
        payload: bytes,
    ) -> None:
        assert hashlib.sha256(payload).hexdigest() == reference.content_sha256
        self.payloads[
            (
                reference.artifact_identifier,
                reference.content_sha256,
            )
        ] = payload


def _invocation(
    adapter: RDKitCheminformaticsAdapter,
    capability_name: str,
    *,
    inputs: tuple[ArtifactReference, ...] = (),
    parameters: dict[str, object] | None = None,
    execution_identifier: str = "execution.phase8-advanced-geometry",
) -> CapabilityInvocation:
    envelope = next(
        item
        for item in adapter.declaration.capabilities
        if item.descriptor.capability_name == capability_name
    )
    return CapabilityInvocation(
        capability=envelope.descriptor,
        input_artifacts=inputs,
        experiment=EXPERIMENT,
        context=ExecutionContext(
            execution_id=execution_identifier,
        ),
        parameters=parameters or {},
    )


def _graph(
    smiles: str,
    *,
    prepare: bool,
) -> MolecularGraph:
    store = MemoryPayloadStore()
    adapter = RDKitCheminformaticsAdapter(store)

    parsed = adapter.invoke(
        _invocation(
            adapter,
            SMILES_PARSE,
            parameters={"smiles": smiles},
        )
    )
    assert parsed.status is ExecutionStatus.SUCCESS

    source = parsed.output_artifacts[0]
    if not prepare:
        return MolecularGraph.model_validate_json(
            store.read(source)
        )

    prepared = adapter.invoke(
        _invocation(
            adapter,
            PREPARE,
            inputs=(source,),
        )
    )
    assert prepared.status is ExecutionStatus.SUCCESS

    return MolecularGraph.model_validate_json(
        store.read(prepared.output_artifacts[0])
    )


def test_resolves_ester_substructure_and_bonds() -> None:
    pytest.importorskip("rdkit")

    graph = _graph(
        "CC(=O)OC",
        prepare=False,
    )

    resolution = resolve_smarts_substructure(
        graph,
        query_smarts="[CX3](=O)[OX2][#6]",
        require_unique=True,
    )

    assert isinstance(
        resolution,
        MolecularSubstructureResolution,
    )
    assert len(resolution.matches) == 1
    assert len(resolution.matches[0].atom_indices) == 4
    assert len(resolution.matches[0].bond_indices) >= 3


def test_missing_substructure_fails_closed() -> None:
    pytest.importorskip("rdkit")

    graph = _graph(
        "CCO",
        prepare=False,
    )

    with pytest.raises(
        ValueError,
        match="does not resolve",
    ):
        resolve_smarts_substructure(
            graph,
            query_smarts="[Zn]",
            require_unique=True,
        )


def test_constrained_bond_scan_generates_requested_points() -> None:
    pytest.importorskip("rdkit")

    graph = _graph(
        "CC",
        prepare=True,
    )

    conformers, scan = constrained_bond_distance_scan(
        graph,
        atom_index_a=0,
        atom_index_b=1,
        start_distance_angstrom=1.3,
        stop_distance_angstrom=1.7,
        point_count=5,
        random_seed=23,
        maximum_iterations=500,
        force_constant=5000.0,
    )

    assert isinstance(scan, MolecularDistanceScan)
    assert scan.point_count == 5
    assert len(scan.points) == 5
    assert len(conformers.conformers) == 5

    assert tuple(
        point.requested_distance_angstrom
        for point in scan.points
    ) == (
        1.3,
        1.4,
        1.5,
        1.6,
        1.7,
    )

    assert all(
        abs(
            point.observed_distance_angstrom
            - point.requested_distance_angstrom
        )
        < 0.05
        for point in scan.points
    )


def test_distance_scan_is_byte_deterministic() -> None:
    pytest.importorskip("rdkit")

    graph = _graph(
        "CC",
        prepare=True,
    )

    first_conformers, first_scan = constrained_bond_distance_scan(
        graph,
        atom_index_a=0,
        atom_index_b=1,
        start_distance_angstrom=1.4,
        stop_distance_angstrom=1.6,
        point_count=3,
        random_seed=31,
        force_constant=5000.0,
    )

    second_conformers, second_scan = constrained_bond_distance_scan(
        graph,
        atom_index_a=0,
        atom_index_b=1,
        start_distance_angstrom=1.4,
        stop_distance_angstrom=1.6,
        point_count=3,
        random_seed=31,
        force_constant=5000.0,
    )

    assert (
        first_conformers.to_canonical_json()
        == second_conformers.to_canonical_json()
    )
    assert (
        first_scan.to_canonical_json()
        == second_scan.to_canonical_json()
    )


def test_distance_scan_requires_explicit_bond() -> None:
    pytest.importorskip("rdkit")

    graph = _graph(
        "CCC",
        prepare=True,
    )

    with pytest.raises(
        ValueError,
        match="explicit graph bond",
    ):
        constrained_bond_distance_scan(
            graph,
            atom_index_a=0,
            atom_index_b=2,
            start_distance_angstrom=2.0,
            stop_distance_angstrom=2.5,
            point_count=3,
        )

def test_substructure_resolution_executes_through_rdkit_adapter() -> None:
    pytest.importorskip("rdkit")

    store = MemoryPayloadStore()
    adapter = RDKitCheminformaticsAdapter(store)

    parsed = adapter.invoke(
        _invocation(
            adapter,
            SMILES_PARSE,
            parameters={"smiles": "CC(=O)OC"},
        )
    )
    assert parsed.status is ExecutionStatus.SUCCESS

    result = adapter.invoke(
        _invocation(
            adapter,
            SUBSTRUCTURE_RESOLUTION,
            inputs=(parsed.output_artifacts[0],),
            parameters={
                "query_smarts": "[CX3](=O)[OX2][#6]",
                "require_unique": True,
            },
            execution_identifier="execution.phase8-substructure-adapter",
        )
    )

    assert result.status is ExecutionStatus.SUCCESS
    assert result.failure is None
    assert len(result.output_artifacts) == 1

    artifact = result.output_artifacts[0]
    assert artifact.artifact_type == "molecular_substructure_resolution"

    resolution = MolecularSubstructureResolution.model_validate_json(
        store.read(artifact)
    )
    assert len(resolution.matches) == 1
    assert len(resolution.matches[0].atom_indices) == 4
    assert result.lineage[0].source == parsed.output_artifacts[0].pointer


def test_constrained_distance_scan_executes_through_rdkit_adapter() -> None:
    pytest.importorskip("rdkit")

    store = MemoryPayloadStore()
    adapter = RDKitCheminformaticsAdapter(store)

    parsed = adapter.invoke(
        _invocation(
            adapter,
            SMILES_PARSE,
            parameters={"smiles": "CC"},
        )
    )
    assert parsed.status is ExecutionStatus.SUCCESS

    prepared = adapter.invoke(
        _invocation(
            adapter,
            PREPARE,
            inputs=(parsed.output_artifacts[0],),
        )
    )
    assert prepared.status is ExecutionStatus.SUCCESS

    result = adapter.invoke(
        _invocation(
            adapter,
            CONSTRAINED_DISTANCE_SCAN,
            inputs=(prepared.output_artifacts[0],),
            parameters={
                "atom_index_a": 0,
                "atom_index_b": 1,
                "start_distance_angstrom": 1.2,
                "stop_distance_angstrom": 2.8,
                "point_count": 15,
                "random_seed": 43,
                "maximum_iterations": 500,
                "force_constant": 5000.0,
            },
            execution_identifier="execution.phase8-distance-scan-adapter",
        )
    )

    assert result.status is ExecutionStatus.SUCCESS
    assert result.failure is None
    assert tuple(
        artifact.artifact_type
        for artifact in result.output_artifacts
    ) == (
        "molecular_conformer_set",
        "molecular_distance_scan",
    )

    scan = MolecularDistanceScan.model_validate_json(
        store.read(result.output_artifacts[1])
    )
    assert scan.point_count == 15
    assert scan.start_distance_angstrom == 1.2
    assert scan.stop_distance_angstrom == 2.8
    assert len(scan.points) == 15
    assert all(
        abs(
            point.requested_distance_angstrom
            - point.observed_distance_angstrom
        )
        < 0.05
        for point in scan.points
    )

