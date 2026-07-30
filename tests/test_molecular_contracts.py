import hashlib
from collections.abc import Callable
from typing import Any, TypeVar

import pytest
from pydantic import ValidationError

from cgr.kernel.contracts import CapabilityVersion
from cgr.molecular import (
    MolecularComponent,
    MolecularMemberReference,
    MolecularProject,
    MolecularRegion,
    MolecularSceneManifest,
    MolecularSelection,
    MolecularStructure,
    MolecularSystem,
)
from cgr.science import (
    ArtifactLineageEdge,
    ArtifactLineageGraph,
    ArtifactReference,
    CanonicalModel,
    CreationProvenance,
)

SCHEMA_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
PROVENANCE = CreationProvenance(
    producer="molecular-contract-tests",
    producer_version=SCHEMA_VERSION,
    execution_identifier="execution-molecular-contracts",
    source="test-suite",
)

_Contract = TypeVar("_Contract", bound=CanonicalModel)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _artifact(
    identifier: str,
    artifact_type: str,
    media_type: str,
) -> ArtifactReference:
    return ArtifactReference(
        artifact_identifier=identifier,
        schema_version=SCHEMA_VERSION,
        artifact_type=artifact_type,
        media_type=media_type,
        content_sha256=_sha256(identifier),
        byte_size=4096,
        storage_location=f"artifact://molecular/{identifier}",
        provenance=PROVENANCE,
    )


def _replace(contract: _Contract, **updates: Any) -> _Contract:
    values = {
        field_name: getattr(contract, field_name)
        for field_name in type(contract).model_fields
    }
    values.update(updates)
    return type(contract)(**values)


def _valid_project() -> MolecularProject:
    structure_alpha_artifact = _artifact(
        "artifact-structure-alpha",
        "molecular_structure",
        "chemical/x-mmcif",
    )
    topology_alpha_artifact = _artifact(
        "artifact-topology-alpha",
        "molecular_topology",
        "application/json",
    )
    structure_beta_artifact = _artifact(
        "artifact-structure-beta",
        "molecular_structure",
        "chemical/x-pdb",
    )
    structure_gamma_artifact = _artifact(
        "artifact-structure-gamma",
        "molecular_structure",
        "chemical/x-sdf",
    )

    structures = (
        MolecularStructure(
            structure_identifier="structure-alpha",
            system_identifier="system-complex",
            coordinate_unit="angstrom",
            structure_artifact=structure_alpha_artifact,
            topology_artifact=topology_alpha_artifact,
            atom_count=120_000,
            bond_count=121_000,
            residue_count=680,
            chain_count=4,
            model_count=1,
            frame_count=250,
        ),
        MolecularStructure(
            structure_identifier="structure-beta",
            system_identifier="system-complex",
            coordinate_unit="nanometer",
            structure_artifact=structure_beta_artifact,
            atom_count=42,
            bond_count=41,
            residue_count=2,
            chain_count=1,
            model_count=2,
            frame_count=1,
        ),
        MolecularStructure(
            structure_identifier="structure-gamma",
            system_identifier="system-small",
            coordinate_unit="bohr",
            structure_artifact=structure_gamma_artifact,
            atom_count=24,
            bond_count=25,
            residue_count=0,
            chain_count=0,
            model_count=1,
            frame_count=1,
        ),
    )
    systems = (
        MolecularSystem(
            system_identifier="system-complex",
            label="Protein-ligand complex",
            structure_identifiers=("structure-beta", "structure-alpha"),
            default_structure_identifier="structure-alpha",
        ),
        MolecularSystem(
            system_identifier="system-small",
            label="Imported molecular design",
            structure_identifiers=("structure-gamma",),
            default_structure_identifier="structure-gamma",
        ),
    )
    components = (
        MolecularComponent(
            component_identifier="component-protein",
            structure_identifier="structure-alpha",
            component_type="protein",
            label="Target protein",
            members=(
                MolecularMemberReference(
                    member_kind="chain",
                    member_identifier="chain-a",
                ),
                MolecularMemberReference(
                    member_kind="residue",
                    member_identifier="residue-42",
                ),
            ),
        ),
        MolecularComponent(
            component_identifier="component-ligand",
            structure_identifier="structure-alpha",
            component_type="ligand",
            members=(
                MolecularMemberReference(
                    member_kind="atom",
                    member_identifier="atom-100001",
                ),
            ),
        ),
        MolecularComponent(
            component_identifier="component-solvent",
            structure_identifier="structure-beta",
            component_type="solvent",
            members=(
                MolecularMemberReference(
                    member_kind="residue",
                    member_identifier="residue-solvent-1",
                ),
            ),
        ),
    )
    selections = (
        MolecularSelection(
            selection_identifier="selection-pocket",
            structure_identifier="structure-alpha",
            label="Imported binding pocket",
            selection_source="imported",
            members=(
                MolecularMemberReference(
                    member_kind="atom",
                    member_identifier="atom-100001",
                ),
                MolecularMemberReference(
                    member_kind="residue",
                    member_identifier="residue-42",
                ),
                MolecularMemberReference(
                    member_kind="chain",
                    member_identifier="chain-a",
                ),
                MolecularMemberReference(
                    member_kind="component",
                    member_identifier="component-ligand",
                ),
            ),
        ),
        MolecularSelection(
            selection_identifier="selection-ligand",
            structure_identifier="structure-alpha",
            selection_source="user",
            members=(
                MolecularMemberReference(
                    member_kind="component",
                    member_identifier="component-ligand",
                ),
            ),
        ),
        MolecularSelection(
            selection_identifier="selection-solvent",
            structure_identifier="structure-beta",
            selection_source="model_proposed",
            members=(
                MolecularMemberReference(
                    member_kind="component",
                    member_identifier="component-solvent",
                ),
            ),
        ),
    )
    regions = (
        MolecularRegion(
            region_identifier="region-pocket",
            structure_identifier="structure-alpha",
            region_type="binding_pocket",
            selection_identifier="selection-pocket",
        ),
        MolecularRegion(
            region_identifier="region-refined-pocket",
            structure_identifier="structure-alpha",
            region_type="derived",
            selection_identifier="selection-ligand",
            parent_region_identifiers=("region-pocket",),
        ),
    )
    scene = MolecularSceneManifest(
        scene_identifier="scene-complex",
        schema_version=SCHEMA_VERSION,
        project_identifier="project-universal",
        structure_identifiers=("structure-beta", "structure-alpha"),
        primary_structure_identifier="structure-alpha",
        selection_identifiers=(
            "selection-solvent",
            "selection-pocket",
            "selection-ligand",
        ),
        region_identifiers=("region-refined-pocket", "region-pocket"),
        artifact_references=(
            topology_alpha_artifact,
            structure_alpha_artifact,
        ),
    )
    lineage = ArtifactLineageGraph(
        edges=(
            ArtifactLineageEdge(
                source=structure_alpha_artifact.pointer,
                destination=topology_alpha_artifact.pointer,
                relationship_type="declares_topology",
                producing_capability="molecular-import",
                producing_capability_version=SCHEMA_VERSION,
                execution_identifier="execution-molecular-contracts",
            ),
        )
    )
    return MolecularProject(
        project_identifier="project-universal",
        schema_version=SCHEMA_VERSION,
        systems=systems,
        structures=structures,
        components=components,
        selections=selections,
        regions=regions,
        scenes=(scene,),
        provenance=PROVENANCE,
        lineage=lineage,
    )


def test_valid_project_supports_large_multi_system_artifact_backed_models() -> None:
    project = _valid_project()

    assert len(project.systems) == 2
    assert len(project.structures) == 3
    assert project.structures[0].atom_count == 120_000
    assert project.structures[0].topology_artifact is not None
    assert isinstance(project.structures[0].structure_artifact, ArtifactReference)
    assert isinstance(project.lineage, ArtifactLineageGraph)
    assert project.lineage.edges
    assert {component.component_type for component in project.components} == {
        "ligand",
        "protein",
        "solvent",
    }
    member_kinds = {
        member.member_kind
        for selection in project.selections
        for member in selection.members
    }
    assert member_kinds == {"atom", "residue", "chain", "component"}
    assert project.regions[1].parent_region_identifiers == ("region-pocket",)


def test_project_fingerprint_is_deterministic_for_reordered_collections() -> None:
    project = _valid_project()
    reordered_components = tuple(
        _replace(component, members=tuple(reversed(component.members)))
        for component in reversed(project.components)
    )
    reordered_selections = tuple(
        _replace(selection, members=tuple(reversed(selection.members)))
        for selection in reversed(project.selections)
    )
    scene = project.scenes[0]
    reordered_scene = _replace(
        scene,
        structure_identifiers=tuple(reversed(scene.structure_identifiers)),
        selection_identifiers=tuple(reversed(scene.selection_identifiers)),
        region_identifiers=tuple(reversed(scene.region_identifiers)),
        artifact_references=tuple(reversed(scene.artifact_references)),
    )
    reordered = _replace(
        project,
        systems=tuple(reversed(project.systems)),
        structures=tuple(reversed(project.structures)),
        components=reordered_components,
        selections=reordered_selections,
        regions=tuple(reversed(project.regions)),
        scenes=(reordered_scene,),
    )

    assert reordered.to_canonical_json() == project.to_canonical_json()
    assert reordered.fingerprint == project.fingerprint


@pytest.mark.parametrize(
    "collection_name",
    ["systems", "structures", "components", "selections", "regions", "scenes"],
)
def test_project_rejects_duplicate_collection_identifiers(
    collection_name: str,
) -> None:
    project = _valid_project()
    collection = getattr(project, collection_name)

    with pytest.raises(ValidationError, match="identifiers must be unique"):
        _replace(project, **{collection_name: (*collection, collection[0])})


def test_contracts_reject_duplicate_nested_references() -> None:
    project = _valid_project()
    system = project.systems[0]
    selection = project.selections[0]
    scene = project.scenes[0]

    with pytest.raises(ValidationError, match="unique"):
        _replace(
            system,
            structure_identifiers=(
                system.structure_identifiers[0],
                system.structure_identifiers[0],
            ),
        )
    with pytest.raises(ValidationError, match="unique"):
        _replace(selection, members=(selection.members[0], selection.members[0]))
    with pytest.raises(ValidationError, match="unique"):
        _replace(
            scene,
            artifact_references=(
                scene.artifact_references[0],
                scene.artifact_references[0],
            ),
        )


def test_project_rejects_structure_with_missing_system() -> None:
    project = _valid_project()
    invalid_structure = _replace(
        project.structures[0],
        system_identifier="system-missing",
    )

    with pytest.raises(ValidationError, match="existing system"):
        _replace(
            project,
            structures=(invalid_structure, *project.structures[1:]),
        )


def test_project_rejects_missing_or_cross_system_structure_references() -> None:
    project = _valid_project()
    system = project.systems[0]

    with pytest.raises(ValidationError, match="must resolve"):
        _replace(
            project,
            systems=(
                _replace(
                    system,
                    structure_identifiers=("structure-missing",),
                    default_structure_identifier="structure-missing",
                ),
                project.systems[1],
            ),
        )

    with pytest.raises(ValidationError, match="same system identifier"):
        _replace(
            project,
            systems=(
                _replace(
                    system,
                    structure_identifiers=("structure-gamma",),
                    default_structure_identifier="structure-gamma",
                ),
                project.systems[1],
            ),
        )


@pytest.mark.parametrize(
    ("collection_name", "contract_factory"),
    [
        (
            "components",
            lambda project: _replace(
                project.components[0],
                structure_identifier="structure-missing",
            ),
        ),
        (
            "selections",
            lambda project: _replace(
                project.selections[0],
                structure_identifier="structure-missing",
            ),
        ),
        (
            "regions",
            lambda project: _replace(
                project.regions[0],
                structure_identifier="structure-missing",
            ),
        ),
    ],
)
def test_project_rejects_missing_structure_references(
    collection_name: str,
    contract_factory: Callable[[MolecularProject], CanonicalModel],
) -> None:
    project = _valid_project()
    collection = getattr(project, collection_name)

    with pytest.raises(ValidationError, match="existing structure"):
        _replace(
            project,
            **{collection_name: (contract_factory(project), *collection[1:])},
        )


def test_project_rejects_missing_and_cross_structure_region_selections() -> None:
    project = _valid_project()
    region = project.regions[0]

    with pytest.raises(ValidationError, match="selection must resolve"):
        _replace(
            project,
            regions=(
                _replace(region, selection_identifier="selection-missing"),
                project.regions[1],
            ),
        )

    with pytest.raises(ValidationError, match="same structure"):
        _replace(
            project,
            regions=(
                _replace(region, selection_identifier="selection-solvent"),
                project.regions[1],
            ),
        )


def test_project_rejects_missing_cross_structure_and_cyclic_region_parents() -> None:
    project = _valid_project()
    pocket, refined = project.regions

    with pytest.raises(ValidationError, match="parent molecular region must resolve"):
        _replace(
            project,
            regions=(
                pocket,
                _replace(
                    refined,
                    parent_region_identifiers=("region-missing",),
                ),
            ),
        )

    cross_structure_parent = MolecularRegion(
        region_identifier="region-solvent",
        structure_identifier="structure-beta",
        region_type="solvent_shell",
        selection_identifier="selection-solvent",
    )
    with pytest.raises(ValidationError, match="same structure"):
        _replace(
            project,
            regions=(
                _replace(
                    pocket,
                    parent_region_identifiers=("region-solvent",),
                ),
                refined,
                cross_structure_parent,
            ),
        )

    with pytest.raises(ValidationError, match="cannot contain cycles"):
        _replace(
            project,
            regions=(
                _replace(
                    pocket,
                    parent_region_identifiers=("region-refined-pocket",),
                ),
                refined,
            ),
        )


def test_region_rejects_direct_self_parenting() -> None:
    with pytest.raises(ValidationError, match="cannot be its own parent"):
        MolecularRegion(
            region_identifier="region-self",
            structure_identifier="structure-alpha",
            region_type="declared",
            selection_identifier="selection-pocket",
            parent_region_identifiers=("region-self",),
        )


@pytest.mark.parametrize(
    ("scene_update", "message"),
    [
        (
            {"structure_identifiers": ("structure-missing",),
             "primary_structure_identifier": "structure-missing"},
            "scene structure must resolve",
        ),
        (
            {"selection_identifiers": ("selection-missing",)},
            "scene selection must resolve",
        ),
        (
            {"region_identifiers": ("region-missing",)},
            "scene region must resolve",
        ),
        (
            {
                "structure_identifiers": ("structure-alpha",),
                "primary_structure_identifier": "structure-alpha",
                "selection_identifiers": ("selection-solvent",),
                "region_identifiers": (),
            },
            "selection.*scene structure",
        ),
    ],
)
def test_project_rejects_unresolved_or_out_of_scene_references(
    scene_update: dict[str, Any],
    message: str,
) -> None:
    project = _valid_project()
    invalid_scene = _replace(project.scenes[0], **scene_update)

    with pytest.raises(ValidationError, match=message):
        _replace(project, scenes=(invalid_scene,))


def test_project_rejects_scene_region_outside_scene_structures() -> None:
    project = _valid_project()
    solvent_region = MolecularRegion(
        region_identifier="region-solvent",
        structure_identifier="structure-beta",
        region_type="solvent_shell",
        selection_identifier="selection-solvent",
    )
    invalid_scene = _replace(
        project.scenes[0],
        structure_identifiers=("structure-alpha",),
        primary_structure_identifier="structure-alpha",
        selection_identifiers=(),
        region_identifiers=("region-solvent",),
    )

    with pytest.raises(ValidationError, match="region.*scene structure"):
        _replace(
            project,
            regions=(*project.regions, solvent_region),
            scenes=(invalid_scene,),
        )


def test_project_rejects_scene_for_another_project() -> None:
    project = _valid_project()
    invalid_scene = _replace(
        project.scenes[0],
        project_identifier="project-other",
    )

    with pytest.raises(ValidationError, match="containing molecular project"):
        _replace(project, scenes=(invalid_scene,))


@pytest.mark.parametrize(
    "contract_factory",
    [
        lambda: _replace(
            _valid_project(),
            schema_version=CapabilityVersion(major=1, minor=1, patch=0),
        ),
        lambda: _replace(
            _valid_project().scenes[0],
            schema_version=CapabilityVersion(major=2, minor=0, patch=0),
        ),
    ],
)
def test_contracts_reject_unsupported_schema_versions(
    contract_factory: Callable[[], CanonicalModel],
) -> None:
    with pytest.raises(ValidationError, match="exactly 1.0.0"):
        contract_factory()


def test_structure_rejects_invalid_coordinate_units_without_count_ceiling() -> None:
    structure = _valid_project().structures[0]
    assert _replace(structure, atom_count=1_000_000_000).atom_count == 1_000_000_000

    with pytest.raises(ValidationError):
        _replace(structure, coordinate_unit="picometer")


@pytest.mark.parametrize(
    "contract_factory",
    [
        lambda: MolecularSystem(
            system_identifier="system-empty",
            structure_identifiers=(),
            default_structure_identifier="structure-empty",
        ),
        lambda: MolecularComponent(
            component_identifier="component-empty",
            structure_identifier="structure-alpha",
            component_type="other",
            members=(),
        ),
        lambda: MolecularSelection(
            selection_identifier="selection-empty",
            structure_identifier="structure-alpha",
            selection_source="declared",
            members=(),
        ),
        lambda: MolecularSceneManifest(
            scene_identifier="scene-empty",
            schema_version=SCHEMA_VERSION,
            project_identifier="project-empty",
            structure_identifiers=(),
            primary_structure_identifier="structure-empty",
        ),
        lambda: _replace(_valid_project(), systems=()),
        lambda: _replace(_valid_project(), structures=()),
    ],
)
def test_contracts_reject_empty_required_tuples(
    contract_factory: Callable[[], CanonicalModel],
) -> None:
    with pytest.raises(ValidationError):
        contract_factory()


def test_default_and_primary_structures_must_be_declared() -> None:
    project = _valid_project()

    with pytest.raises(ValidationError, match="default structure"):
        _replace(
            project.systems[0],
            default_structure_identifier="structure-gamma",
        )
    with pytest.raises(ValidationError, match="primary structure"):
        _replace(
            project.scenes[0],
            primary_structure_identifier="structure-gamma",
        )


def test_member_identifiers_and_open_type_fields_remain_generic() -> None:
    member = MolecularMemberReference(
        member_kind="atom",
        member_identifier="atom-arbitrary:900000",
    )
    component = MolecularComponent(
        component_identifier="component-future",
        structure_identifier="structure-alpha",
        component_type="future_scientific_component",
        members=(member,),
    )
    selection = MolecularSelection(
        selection_identifier="selection-future",
        structure_identifier="structure-alpha",
        selection_source="future_selection_source",
        members=(member,),
    )
    region = MolecularRegion(
        region_identifier="region-future",
        structure_identifier="structure-alpha",
        region_type="future_structural_region",
        selection_identifier="selection-future",
    )

    assert component.component_type == "future_scientific_component"
    assert selection.selection_source == "future_selection_source"
    assert region.region_type == "future_structural_region"
