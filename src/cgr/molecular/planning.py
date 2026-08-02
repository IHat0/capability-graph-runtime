"""Deterministic projection of molecular project evidence into planning facts."""

from __future__ import annotations

from collections import Counter

from cgr.science.artifacts import ArtifactPointer
from cgr.science.canonical import sha256_fingerprint
from cgr.science.planning import PlanningFact, PlanningFactSet

from .contracts import MolecularMemberReference, MolecularProject
from .topology import MolecularTopologyIndex

_DERIVATION_IDENTIFIER = "molecular-planning-projection-v1"
_STRUCTURE_FORMATS = ("mmcif", "mol", "pdb", "sdf", "xyz")


class MolecularPlanningProjectionError(Exception):
    """Raised when molecular evidence cannot be projected safely."""


def _ordered_evidence(
    values: tuple[ArtifactPointer, ...],
) -> tuple[ArtifactPointer, ...]:
    unique = {
        (item.artifact_identifier, item.content_sha256): item for item in values
    }
    return tuple(unique[key] for key in sorted(unique))


def _fact(
    *,
    project: MolecularProject,
    subject: str,
    dimension: str,
    unit: str,
    value: bool | int | float,
    evidence: tuple[ArtifactPointer, ...] = (),
) -> PlanningFact:
    identity = {
        "subject_identifier": subject,
        "dimension": dimension,
        "unit": unit,
        "derivation_identifier": _DERIVATION_IDENTIFIER,
    }
    return PlanningFact(
        fact_identifier=f"planning-fact-{sha256_fingerprint(identity)[:32]}",
        schema_version=project.schema_version,
        subject_identifier=subject,
        dimension=dimension,
        unit=unit,
        value=value,
        evidence_artifacts=_ordered_evidence(evidence),
        derivation_identifier=_DERIVATION_IDENTIFIER,
    )


def _validate_topologies(
    project: MolecularProject,
    topologies: tuple[MolecularTopologyIndex, ...],
) -> dict[str, MolecularTopologyIndex]:
    structures = {item.structure_identifier: item for item in project.structures}
    indexed: dict[str, MolecularTopologyIndex] = {}
    for topology in topologies:
        structure = structures.get(topology.structure_identifier)
        if structure is None:
            raise MolecularPlanningProjectionError(
                "A topology index references an unknown molecular structure."
            )
        if topology.structure_identifier in indexed:
            raise MolecularPlanningProjectionError(
                "A molecular structure has more than one topology index."
            )
        if structure.topology_artifact is None:
            raise MolecularPlanningProjectionError(
                "A topology index requires a declared topology artifact."
            )
        if topology.source_structure != structure.structure_artifact.pointer:
            raise MolecularPlanningProjectionError(
                "Topology source identity does not match its molecular structure."
            )
        if topology.coordinate_unit != structure.coordinate_unit:
            raise MolecularPlanningProjectionError(
                "Topology coordinate units do not match the molecular structure."
            )
        expected_counts = (
            structure.atom_count,
            structure.bond_count,
            structure.residue_count,
            structure.chain_count,
            structure.model_count,
            structure.frame_count,
        )
        actual_counts = (
            len(topology.atoms),
            len(topology.bonds),
            len(topology.residues),
            len(topology.chains),
            topology.model_count,
            topology.frame_count,
        )
        if actual_counts != expected_counts:
            raise MolecularPlanningProjectionError(
                "Topology counts do not match the molecular structure."
            )
        indexed[topology.structure_identifier] = topology
    return indexed


class _MemberResolver:
    def __init__(self, project: MolecularProject, topology: MolecularTopologyIndex):
        self._topology = topology
        self._atoms = {item.atom_identifier: item for item in topology.atoms}
        self._residues = {
            item.residue_identifier: set(item.atom_identifiers)
            for item in topology.residues
        }
        self._chains = {
            item.chain_identifier: set(item.atom_identifiers)
            for item in topology.chains
        }
        self._components = {
            item.component_identifier: item
            for item in project.components
            if item.structure_identifier == topology.structure_identifier
        }
        self._component_cache: dict[str, set[str]] = {}
        self._visiting_components: set[str] = set()

    def members(self, members: tuple[MolecularMemberReference, ...]) -> set[str]:
        atoms: set[str] = set()
        for member in members:
            atoms.update(self.member(member))
        return atoms

    def member(self, member: MolecularMemberReference) -> set[str]:
        identifier = member.member_identifier
        if member.member_kind == "atom":
            if identifier not in self._atoms:
                self._unresolved()
            return {identifier}
        if member.member_kind == "residue":
            if identifier not in self._residues:
                self._unresolved()
            return set(self._residues[identifier])
        if member.member_kind == "chain":
            if identifier not in self._chains:
                self._unresolved()
            return set(self._chains[identifier])
        return self.component(identifier)

    def component(self, identifier: str) -> set[str]:
        if identifier in self._component_cache:
            return set(self._component_cache[identifier])
        if identifier in self._visiting_components:
            raise MolecularPlanningProjectionError(
                "Molecular component membership contains a cycle."
            )
        self._visiting_components.add(identifier)
        atoms = {
            atom.atom_identifier
            for atom in self._topology.atoms
            if atom.component_identifier == identifier
        }
        declaration = self._components.get(identifier)
        if declaration is not None:
            atoms.update(self.members(declaration.members))
        if not atoms:
            self._visiting_components.remove(identifier)
            self._unresolved()
        self._visiting_components.remove(identifier)
        self._component_cache[identifier] = atoms
        return set(atoms)

    def counts(self, atom_identifiers: set[str]) -> tuple[int, int, int, int]:
        atoms = [self._atoms[identifier] for identifier in sorted(atom_identifiers)]
        residues = {
            atom.residue_identifier
            for atom in atoms
            if atom.residue_identifier is not None
        }
        residues.update(
            identifier
            for identifier, members in self._residues.items()
            if members.intersection(atom_identifiers)
        )
        chains = {
            atom.chain_identifier for atom in atoms if atom.chain_identifier is not None
        }
        chains.update(
            identifier
            for identifier, members in self._chains.items()
            if members.intersection(atom_identifiers)
        )
        components = {
            atom.component_identifier
            for atom in atoms
            if atom.component_identifier is not None
        }
        for identifier in self._components:
            if self.component(identifier).intersection(atom_identifiers):
                components.add(identifier)
        return len(atoms), len(residues), len(chains), len(components)

    @staticmethod
    def _unresolved() -> None:
        raise MolecularPlanningProjectionError(
            "A structural member cannot be resolved by the supplied topology."
        )


def _count_facts(
    *,
    project: MolecularProject,
    subject: str,
    prefix: str,
    counts: tuple[int, int, int, int],
    evidence: tuple[ArtifactPointer, ...],
) -> tuple[PlanningFact, ...]:
    return tuple(
        _fact(
            project=project,
            subject=subject,
            dimension=f"{prefix}.{label}_count",
            unit="count",
            value=value,
            evidence=evidence,
        )
        for label, value in zip(
            ("atom", "residue", "chain", "component"), counts, strict=True
        )
    )


def project_molecular_planning_facts(
    *,
    project: MolecularProject,
    topology_indices: tuple[MolecularTopologyIndex, ...] = (),
) -> PlanningFactSet:
    """Project validated molecular evidence without scientific inference."""
    topologies = _validate_topologies(project, topology_indices)
    structure_evidence = tuple(
        item.structure_artifact.pointer for item in project.structures
    )
    facts: list[PlanningFact] = []

    for label, value in (
        ("system", len(project.systems)),
        ("structure", len(project.structures)),
        ("component", len(project.components)),
        ("selection", len(project.selections)),
        ("region", len(project.regions)),
        ("scene", len(project.scenes)),
    ):
        facts.append(
            _fact(
                project=project,
                subject=project.project_identifier,
                dimension=f"project.{label}_count",
                unit="count",
                value=value,
                evidence=structure_evidence,
            )
        )

    structures = {item.structure_identifier: item for item in project.structures}
    for system in project.systems:
        evidence = tuple(
            structures[item].structure_artifact.pointer
            for item in system.structure_identifiers
        )
        facts.append(
            _fact(
                project=project,
                subject=system.system_identifier,
                dimension="system.structure_count",
                unit="count",
                value=len(system.structure_identifiers),
                evidence=evidence,
            )
        )

    for structure in project.structures:
        coordinate_evidence = (structure.structure_artifact.pointer,)
        for name in (
            "atom",
            "bond",
            "residue",
            "chain",
            "model",
            "frame",
        ):
            facts.append(
                _fact(
                    project=project,
                    subject=structure.structure_identifier,
                    dimension=f"structure.{name}_count",
                    unit="count",
                    value=getattr(structure, f"{name}_count"),
                    evidence=coordinate_evidence,
                )
            )
        topology = topologies.get(structure.structure_identifier)
        topology_evidence = (
            (structure.topology_artifact.pointer,)
            if structure.topology_artifact is not None
            else coordinate_evidence
        )
        facts.append(
            _fact(
                project=project,
                subject=structure.structure_identifier,
                dimension="structure.has_topology",
                unit="boolean",
                value=structure.topology_artifact is not None,
                evidence=topology_evidence,
            )
        )
        if topology is None:
            continue
        for structure_format in _STRUCTURE_FORMATS:
            facts.append(
                _fact(
                    project=project,
                    subject=structure.structure_identifier,
                    dimension=f"structure.format.{structure_format}",
                    unit="boolean",
                    value=topology.structure_format == structure_format,
                    evidence=topology_evidence,
                )
            )
        for symbol, count in sorted(
            Counter(atom.element_symbol for atom in topology.atoms).items()
        ):
            facts.append(
                _fact(
                    project=project,
                    subject=structure.structure_identifier,
                    dimension=f"structure.element.{symbol}.count",
                    unit="count",
                    value=count,
                    evidence=topology_evidence,
                )
            )
        known_charges = [
            atom.formal_charge
            for atom in topology.atoms
            if atom.formal_charge is not None
        ]
        facts.append(
            _fact(
                project=project,
                subject=structure.structure_identifier,
                dimension="structure.formal_charge_known_count",
                unit="count",
                value=len(known_charges),
                evidence=topology_evidence,
            )
        )
        if known_charges:
            facts.append(
                _fact(
                    project=project,
                    subject=structure.structure_identifier,
                    dimension="structure.formal_charge_sum",
                    unit="elementary_charge",
                    value=sum(known_charges),
                    evidence=topology_evidence,
                )
            )

    selections = {item.selection_identifier: item for item in project.selections}
    for selection in project.selections:
        topology = topologies.get(selection.structure_identifier)
        if topology is None:
            continue
        structure = structures[selection.structure_identifier]
        resolver = _MemberResolver(project, topology)
        atom_identifiers = resolver.members(selection.members)
        facts.extend(
            _count_facts(
                project=project,
                subject=selection.selection_identifier,
                prefix="selection",
                counts=resolver.counts(atom_identifiers),
                evidence=(structure.topology_artifact.pointer,),
            )
        )

    for region in project.regions:
        structure = structures[region.structure_identifier]
        topology = topologies.get(region.structure_identifier)
        evidence = (
            (structure.topology_artifact.pointer,)
            if topology is not None and structure.topology_artifact is not None
            else (structure.structure_artifact.pointer,)
        )
        facts.append(
            _fact(
                project=project,
                subject=region.region_identifier,
                dimension="region.exists",
                unit="boolean",
                value=True,
                evidence=evidence,
            )
        )
        if topology is None:
            continue
        resolver = _MemberResolver(project, topology)
        atom_identifiers = resolver.members(
            selections[region.selection_identifier].members
        )
        facts.extend(
            _count_facts(
                project=project,
                subject=region.region_identifier,
                prefix="region",
                counts=resolver.counts(atom_identifiers),
                evidence=evidence,
            )
        )

    return PlanningFactSet(facts=tuple(facts))
