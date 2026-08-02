"""Molecular-project to generic planning-fact projection regressions."""

from __future__ import annotations

from typing import Any

import pytest

from cgr.kernel.contracts import CapabilityVersion
from cgr.molecular.contracts import (
    MolecularComponent,
    MolecularMemberReference,
    MolecularProject,
    MolecularRegion,
    MolecularSceneManifest,
    MolecularSelection,
    MolecularStructure,
    MolecularSystem,
)
from cgr.molecular.planning import (
    MolecularPlanningProjectionError,
    project_molecular_planning_facts,
)
from cgr.molecular.topology import (
    MolecularTopologyAtom,
    MolecularTopologyBond,
    MolecularTopologyChain,
    MolecularTopologyIndex,
    MolecularTopologyResidue,
)
from cgr.science.artifacts import ArtifactReference, CreationProvenance

VERSION = CapabilityVersion(major=1, minor=0, patch=0)
PROVENANCE = CreationProvenance(producer="molecular-planning-tests", producer_version=VERSION)


def _artifact(identifier: str, sha: str, artifact_type: str) -> ArtifactReference:
    return ArtifactReference(
        artifact_identifier=identifier,
        schema_version=VERSION,
        artifact_type=artifact_type,
        media_type="application/octet-stream",
        content_sha256=sha * 64,
        byte_size=1,
        provenance=PROVENANCE,
    )


def _topology(structure: MolecularStructure, **updates: Any) -> MolecularTopologyIndex:
    values: dict[str, Any] = {
        "schema_version": VERSION,
        "structure_identifier": structure.structure_identifier,
        "source_structure": structure.structure_artifact.pointer,
        "structure_format": "pdb",
        "parser_identifier": "planning-test-parser",
        "parser_version": "1.0.0",
        "atoms": (
            MolecularTopologyAtom(
                atom_identifier="atom-0",
                atom_index=0,
                element_symbol="C",
                chain_identifier="chain-a",
                residue_identifier="residue-a",
                component_identifier="component-ligand",
                formal_charge=1,
            ),
            MolecularTopologyAtom(
                atom_identifier="atom-1",
                atom_index=1,
                element_symbol="H",
                chain_identifier="chain-a",
                residue_identifier="residue-a",
                component_identifier="component-ligand",
                formal_charge=None,
            ),
            MolecularTopologyAtom(
                atom_identifier="atom-2",
                atom_index=2,
                element_symbol="H",
                chain_identifier="chain-b",
                residue_identifier="residue-b",
                component_identifier="component-solvent",
                formal_charge=-1,
            ),
            MolecularTopologyAtom(
                atom_identifier="atom-3",
                atom_index=3,
                element_symbol="Fe",
                chain_identifier="chain-b",
                residue_identifier="residue-b",
                component_identifier=None,
                formal_charge=None,
            ),
        ),
        "bonds": (
            MolecularTopologyBond(
                bond_identifier="bond-0",
                atom_identifier_a="atom-0",
                atom_identifier_b="atom-1",
                bond_order=1,
                bond_type="single",
            ),
        ),
        "residues": (
            MolecularTopologyResidue(
                residue_identifier="residue-a",
                residue_name="LIG",
                chain_identifier="chain-a",
                atom_identifiers=("atom-0", "atom-1"),
            ),
            MolecularTopologyResidue(
                residue_identifier="residue-b",
                residue_name="SOL",
                chain_identifier="chain-b",
                atom_identifiers=("atom-2", "atom-3"),
            ),
        ),
        "chains": (
            MolecularTopologyChain(
                chain_identifier="chain-a",
                atom_identifiers=("atom-0", "atom-1"),
                residue_identifiers=("residue-a",),
            ),
            MolecularTopologyChain(
                chain_identifier="chain-b",
                atom_identifiers=("atom-2", "atom-3"),
                residue_identifiers=("residue-b",),
            ),
        ),
        "model_count": 1,
        "frame_count": 2,
        "coordinate_unit": "angstrom",
    }
    values.update(updates)
    return MolecularTopologyIndex.model_validate(values)


def _project(*, topology_declared: bool = True, selections: tuple[MolecularSelection, ...] | None = None) -> tuple[MolecularProject, MolecularTopologyIndex]:
    structure_artifact = _artifact("artifact-structure", "a", "molecular_structure")
    topology_artifact = _artifact("artifact-topology", "b", "molecular_topology")
    structure = MolecularStructure(
        structure_identifier="structure-one",
        system_identifier="system-one",
        coordinate_unit="angstrom",
        structure_artifact=structure_artifact,
        topology_artifact=topology_artifact if topology_declared else None,
        atom_count=4,
        bond_count=1,
        residue_count=2,
        chain_count=2,
        model_count=1,
        frame_count=2,
    )
    default_selection = MolecularSelection(
        selection_identifier="selection-one",
        structure_identifier="structure-one",
        selection_source="manual",
        members=(
            MolecularMemberReference(member_kind="atom", member_identifier="atom-0"),
            MolecularMemberReference(member_kind="residue", member_identifier="residue-a"),
            MolecularMemberReference(member_kind="chain", member_identifier="chain-b"),
            MolecularMemberReference(member_kind="component", member_identifier="component-solvent"),
        ),
    )
    selected = (default_selection,) if selections is None else selections
    regions = (
        MolecularRegion(
            region_identifier="region-one",
            structure_identifier="structure-one",
            region_type="structural_region",
            selection_identifier=selected[0].selection_identifier,
        ),
    ) if selected else ()
    components = (
        MolecularComponent(
            component_identifier="component-ligand",
            structure_identifier="structure-one",
            component_type="ligand",
            members=(MolecularMemberReference(member_kind="residue", member_identifier="residue-a"),),
        ),
        MolecularComponent(
            component_identifier="component-solvent",
            structure_identifier="structure-one",
            component_type="solvent",
            members=(MolecularMemberReference(member_kind="atom", member_identifier="atom-2"),),
        ),
    )
    project = MolecularProject(
        project_identifier="project-one",
        schema_version=VERSION,
        systems=(
            MolecularSystem(
                system_identifier="system-one",
                structure_identifiers=("structure-one",),
                default_structure_identifier="structure-one",
            ),
        ),
        structures=(structure,),
        components=components,
        selections=selected,
        regions=regions,
        scenes=(
            MolecularSceneManifest(
                scene_identifier="scene-one",
                schema_version=VERSION,
                project_identifier="project-one",
                structure_identifiers=("structure-one",),
                primary_structure_identifier="structure-one",
            ),
        ),
        provenance=PROVENANCE,
    )
    return project, _topology(structure)


def _value(facts, subject: str, dimension: str, unit: str):
    fact = facts.lookup(subject, dimension, unit)
    assert fact is not None
    return fact.value


def test_direct_project_system_and_structure_facts_need_no_topology() -> None:
    project, _ = _project()
    facts = project_molecular_planning_facts(project=project)
    assert _value(facts, "project-one", "project.system_count", "count") == 1
    assert _value(facts, "project-one", "project.structure_count", "count") == 1
    assert _value(facts, "project-one", "project.component_count", "count") == 2
    assert _value(facts, "project-one", "project.selection_count", "count") == 1
    assert _value(facts, "project-one", "project.region_count", "count") == 1
    assert _value(facts, "project-one", "project.scene_count", "count") == 1
    assert _value(facts, "system-one", "system.structure_count", "count") == 1
    assert _value(facts, "structure-one", "structure.atom_count", "count") == 4
    assert _value(facts, "structure-one", "structure.has_topology", "boolean") is True
    assert _value(facts, "region-one", "region.exists", "boolean") is True
    assert facts.lookup("selection-one", "selection.atom_count", "count") is None
    assert facts.lookup("region-one", "region.atom_count", "count") is None
    project_without_topology, _ = _project(topology_declared=False)
    without_topology = project_molecular_planning_facts(
        project=project_without_topology
    )
    assert _value(
        without_topology,
        "structure-one",
        "structure.has_topology",
        "boolean",
    ) is False


def test_arbitrary_multiple_systems_and_structures_project_exact_counts() -> None:
    project, _ = _project()
    second_structure = MolecularStructure(
        structure_identifier="structure-two",
        system_identifier="system-two",
        coordinate_unit="nanometer",
        structure_artifact=_artifact(
            "artifact-structure-two", "c", "molecular_structure"
        ),
        atom_count=37,
        bond_count=35,
        residue_count=4,
        chain_count=1,
        model_count=2,
        frame_count=20,
    )
    second_system = MolecularSystem(
        system_identifier="system-two",
        structure_identifiers=("structure-two",),
        default_structure_identifier="structure-two",
    )
    expanded = MolecularProject.model_validate(
        {
            **project.model_dump(),
            "systems": (*project.systems, second_system),
            "structures": (*project.structures, second_structure),
        }
    )
    facts = project_molecular_planning_facts(project=expanded)
    assert _value(facts, "project-one", "project.system_count", "count") == 2
    assert _value(facts, "project-one", "project.structure_count", "count") == 2
    assert _value(facts, "system-two", "system.structure_count", "count") == 1
    assert _value(facts, "structure-two", "structure.atom_count", "count") == 37
    assert _value(facts, "structure-two", "structure.frame_count", "count") == 20


def test_topology_projects_formats_elements_charges_and_resolved_unique_counts() -> None:
    project, topology = _project()
    facts = project_molecular_planning_facts(project=project, topology_indices=(topology,))
    assert _value(facts, "structure-one", "structure.has_topology", "boolean") is True
    for structure_format in ("mmcif", "mol", "pdb", "sdf", "xyz"):
        assert _value(
            facts, "structure-one", f"structure.format.{structure_format}", "boolean"
        ) is (structure_format == "pdb")
    assert _value(facts, "structure-one", "structure.element.C.count", "count") == 1
    assert _value(facts, "structure-one", "structure.element.H.count", "count") == 2
    assert _value(facts, "structure-one", "structure.element.Fe.count", "count") == 1
    assert _value(facts, "structure-one", "structure.formal_charge_known_count", "count") == 2
    assert _value(facts, "structure-one", "structure.formal_charge_sum", "elementary_charge") == 0
    for prefix, subject in (("selection", "selection-one"), ("region", "region-one")):
        assert _value(facts, subject, f"{prefix}.atom_count", "count") == 4
        assert _value(facts, subject, f"{prefix}.residue_count", "count") == 2
        assert _value(facts, subject, f"{prefix}.chain_count", "count") == 2
        assert _value(facts, subject, f"{prefix}.component_count", "count") == 2


def test_projection_is_stable_and_never_infers_electronic_active_space() -> None:
    project, topology = _project()
    first = project_molecular_planning_facts(project=project, topology_indices=(topology,))
    second = project_molecular_planning_facts(project=project, topology_indices=(topology,))
    assert first.to_canonical_json() == second.to_canonical_json()
    assert first.fingerprint == second.fingerprint
    forbidden = ("electron", "orbital", "qubit", "active_space", "hamiltonian")
    assert not any(token in fact.dimension for fact in first.facts for token in forbidden)


@pytest.mark.parametrize(
    "mutation",
    (
        "unknown_structure",
        "source",
        "unit",
        "atom_count",
        "model_count",
        "frame_count",
    ),
)
def test_topology_identity_and_counts_fail_closed(mutation: str) -> None:
    project, topology = _project()
    if mutation == "unknown_structure":
        topology = topology.model_copy(update={"structure_identifier": "structure-unknown"})
    elif mutation == "source":
        topology = topology.model_copy(
            update={"source_structure": _artifact("artifact-other", "c", "structure").pointer}
        )
    elif mutation == "unit":
        topology = topology.model_copy(update={"coordinate_unit": "bohr"})
    elif mutation == "atom_count":
        project = project.model_copy(
            update={"structures": (project.structures[0].model_copy(update={"atom_count": 5}),)}
        )
    elif mutation == "model_count":
        topology = topology.model_copy(update={"model_count": 2})
    else:
        topology = topology.model_copy(update={"frame_count": 3})
    with pytest.raises(MolecularPlanningProjectionError):
        project_molecular_planning_facts(project=project, topology_indices=(topology,))


def test_duplicate_or_undeclared_topology_fails_closed() -> None:
    project, topology = _project()
    with pytest.raises(MolecularPlanningProjectionError, match="more than one"):
        project_molecular_planning_facts(
            project=project, topology_indices=(topology, topology)
        )
    project_without, topology_without = _project(topology_declared=False)
    with pytest.raises(MolecularPlanningProjectionError, match="declared topology"):
        project_molecular_planning_facts(
            project=project_without, topology_indices=(topology_without,)
        )


def test_unresolved_selection_member_with_supplied_topology_fails_closed() -> None:
    selection = MolecularSelection(
        selection_identifier="selection-unknown",
        structure_identifier="structure-one",
        selection_source="manual",
        members=(
            MolecularMemberReference(member_kind="atom", member_identifier="atom-unknown"),
        ),
    )
    project, topology = _project(selections=(selection,))
    with pytest.raises(MolecularPlanningProjectionError, match="cannot be resolved"):
        project_molecular_planning_facts(project=project, topology_indices=(topology,))
