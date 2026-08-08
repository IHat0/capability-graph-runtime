"""Docking contracts and optional native-engine declaration tests."""

from __future__ import annotations

import sys

import pytest
from pydantic import ValidationError

from cgr.kernel.contracts import CapabilityVersion, HealthStatus
from cgr.molecular import (
    DOCKING_POSE_GENERATE,
    MolecularDockingPocket,
    MolecularDockingPose,
    MolecularDockingResult,
    VinaDockingAdapter,
    vina_capability_envelopes,
)
from test_rdkit_cheminformatics import MemoryPayloadStore

VERSION = CapabilityVersion(major=1, minor=0, patch=0)


def _pose(rank: int, score: float) -> MolecularDockingPose:
    return MolecularDockingPose(
        pose_identifier=f"pose-{rank}", rank=rank,
        vina_score_kilocalorie_per_mole=score,
        intermolecular_score_kilocalorie_per_mole=score - 0.4,
        intramolecular_score_kilocalorie_per_mole=0.1,
        torsional_score_kilocalorie_per_mole=0.3,
        ligand_atom_serials=(1, 2),
        coordinates_angstrom=((0.0, 0.0, 0.0), (1.3, 0.0, 0.0)),
    )


def test_vina_declaration_is_import_safe_and_explicit_about_score_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "vina", None)
    envelope = vina_capability_envelopes()[0]
    adapter = VinaDockingAdapter(MemoryPayloadStore())

    assert envelope.descriptor.capability_name == DOCKING_POSE_GENERATE
    assert envelope.descriptor.required_tools == ("meeko", "vina")
    assert "binding free energies" in envelope.known_limitations[1]
    assert adapter.health().status is HealthStatus.UNAVAILABLE


def test_docking_result_maps_pose_atoms_and_rejects_free_energy_claims() -> None:
    result = MolecularDockingResult(
        schema_version=VERSION,
        result_identifier="docking-result-1",
        receptor_artifact_identifier="receptor-1",
        ligand_artifact_identifier="ligand-1",
        candidate_identifier="candidate-1",
        engine_version="1.2.7",
        random_seed=7,
        exhaustiveness=8,
        pocket=MolecularDockingPocket(
            pocket_identifier="pocket-1",
            definition_method="explicit_center_box",
            center_angstrom=(0.0, 1.0, 2.0),
            size_angstrom=(18.0, 18.0, 18.0),
            selected_residue_identifiers=("chain-a-residue-42",),
        ),
        poses=(_pose(1, -7.2), _pose(2, -6.4)),
        receptor_preparation_method="meeko-0-7-1",
        ligand_preparation_method="meeko-0-7-1-gasteiger",
    )

    assert not result.binding_free_energy_calculated
    assert result.poses[0].ligand_atom_serials == (1, 2)
    with pytest.raises(ValidationError):
        MolecularDockingResult.model_validate({
            **result.model_dump(mode="python"),
            "binding_free_energy_calculated": True,
        })


def test_docking_pose_ranking_must_follow_vina_score() -> None:
    with pytest.raises(ValidationError):
        MolecularDockingResult(
            schema_version=VERSION,
            result_identifier="docking-result-bad",
            receptor_artifact_identifier="receptor-1",
            ligand_artifact_identifier="ligand-1",
            candidate_identifier="candidate-1",
            engine_version="1.2.7",
            random_seed=7,
            exhaustiveness=8,
            pocket=MolecularDockingPocket(
                pocket_identifier="pocket-1",
                definition_method="explicit_center_box",
                center_angstrom=(0.0, 0.0, 0.0),
                size_angstrom=(18.0, 18.0, 18.0),
            ),
            poses=(_pose(1, -5.0), _pose(2, -7.0)),
            receptor_preparation_method="meeko-0-7-1",
            ligand_preparation_method="meeko-0-7-1-gasteiger",
        )
