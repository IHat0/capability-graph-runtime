"""RDKit-backed Phase 5 adapter using only generic Pulsate artifacts."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from pydantic import ValidationError

from cgr.kernel.contracts import (
    CapabilityVersion,
    ExecutionStatus,
    HealthStatus,
)
from cgr.science import (
    ArtifactLineageEdge,
    ArtifactReference,
    CapabilityDescriptor,
    CapabilityExecutionEnvelope,
    CapabilityInvocation,
    CapabilityResult,
    CreationProvenance,
    DeterminismClassification,
    ExecutionEvidence,
    FailureInformation,
    ScientificEngineAdapterDeclaration,
    ScientificEngineHealthReport,
    ScientificEngineIdentity,
)

from .cheminformatics import (
    MolecularConformer,
    MolecularConformerSet,
    MolecularCoordinate,
    MolecularFragment,
    MolecularFragmentSet,
    MolecularGeometryFinding,
    MolecularGeometryValidation,
    MolecularGraph,
    MolecularGraphAtom,
    MolecularGraphBond,
    MolecularScaffold,
)
from .ingestion import StructureIngestionError, ingest_structure_bytes

_SCHEMA_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
_ADAPTER_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
_EXPECTED_RDKIT_VERSION = "2026.03.4"
_MAXIMUM_PAYLOAD_BYTES = 64 * 1024 * 1024
_MAXIMUM_SMILES_LENGTH = 16_384
_MAXIMUM_ATOMS = 100_000
_MAXIMUM_CONFORMERS = 50

STRUCTURE_INGESTION = "molecular.structure_ingestion"
SMILES_PARSE = "molecular.smiles_parse"
PREPARE = "molecular.rdkit_prepare"
CONFORMER_GENERATION = "molecular.conformer_generate"
GEOMETRY_VALIDATION = "molecular.geometry_validate"
FRAGMENT = "molecular.fragment"
SCAFFOLD = "molecular.scaffold"


@runtime_checkable
class MolecularArtifactPayloadStore(Protocol):
    """Read and persist exact bytes behind generic artifact references."""

    def read(self, reference: ArtifactReference) -> bytes:
        """Return the exact bytes addressed by the supplied reference."""
        ...

    def write(self, reference: ArtifactReference, payload: bytes) -> None:
        """Persist exact bytes under the supplied content-addressed reference."""
        ...


class RDKitAdapterConfigurationError(ValueError):
    """Controlled construction failure for the RDKit adapter."""


def _normalized_version(value: str) -> tuple[int, ...] | None:
    fields = value.strip().split(".")
    if not fields or any(not field.isdigit() for field in fields):
        return None
    return tuple(int(field) for field in fields)


def _descriptor(
    capability_name: str,
    *,
    accepted_artifact_types: tuple[str, ...],
    produced_artifact_types: tuple[str, ...],
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_name=capability_name,
        version=_ADAPTER_VERSION,
        accepted_artifact_types=accepted_artifact_types,
        produced_artifact_types=produced_artifact_types,
        required_tools=("rdkit",),
        required_runtime="python",
        determinism=DeterminismClassification.DETERMINISTIC,
    )


def rdkit_capability_envelopes() -> tuple[CapabilityExecutionEnvelope, ...]:
    """Return deterministic planner declarations for Phase 5.2 capabilities."""

    definitions = (
        (
            STRUCTURE_INGESTION,
            ("molecular_structure",),
            ("molecular_topology",),
            ("structure_ingestion",),
            ("Input bytes must use a supported PDB, mmCIF, MOL, SDF or XYZ format.",),
        ),
        (
            SMILES_PARSE,
            (),
            ("molecular_graph",),
            ("smiles_parsing", "molecular_graph_creation"),
            ("The SMILES value must be supplied explicitly as a parameter.",),
        ),
        (
            PREPARE,
            ("molecular_graph",),
            ("prepared_molecular_graph",),
            ("molecular_preparation",),
            ("Preparation preserves formal charge and source heavy-atom order.",),
        ),
        (
            CONFORMER_GENERATION,
            ("prepared_molecular_graph",),
            ("molecular_conformer_set",),
            ("conformer_generation",),
            (
                "Conformer generation is deterministic only for the pinned RDKit version and fixed seed.",
            ),
        ),
        (
            GEOMETRY_VALIDATION,
            ("molecular_conformer_set",),
            ("molecular_geometry_validation",),
            ("geometry_validation",),
            (
                "Geometry checks detect severe clashes and implausible bonded distances; they are not a quantum-mechanical validation.",
            ),
        ),
        (
            FRAGMENT,
            ("molecular_graph", "prepared_molecular_graph"),
            ("molecular_fragment_set",),
            ("fragment_analysis",),
            ("Fragmentation identifies disconnected molecular components.",),
        ),
        (
            SCAFFOLD,
            ("molecular_graph", "prepared_molecular_graph"),
            ("molecular_scaffold",),
            ("scaffold_analysis",),
            ("Scaffold extraction uses the Bemis-Murcko definition.",),
        ),
    )

    return tuple(
        CapabilityExecutionEnvelope(
            descriptor=_descriptor(
                capability_name,
                accepted_artifact_types=accepted,
                produced_artifact_types=produced,
            ),
            supported_objective_types=objectives,
            execution_targets=("local_cpu",),
            known_limitations=limitations,
            metadata={
                "scientific_domain": "cheminformatics",
                "adapter_family": "rdkit",
            },
        )
        for capability_name, accepted, produced, objectives, limitations in definitions
    )


def _safe_rdkit_version() -> str | None:
    try:
        from rdkit import rdBase
    except ImportError:
        return None
    return str(getattr(rdBase, "rdkitVersion", "unknown")).strip() or "unknown"


def _rdkit_modules() -> tuple[object, object, object]:
    from rdkit import Chem
    from rdkit.Chem import AllChem
    from rdkit.Chem.Scaffolds import MurckoScaffold

    return Chem, AllChem, MurckoScaffold


def _rounded(value: float) -> float:
    return round(float(value), 10)


def _safe_identifier_part(value: str) -> str:
    normalized = "".join(
        character.lower() if character.isalnum() else "-" for character in value
    )
    normalized = "-".join(part for part in normalized.split("-") if part)
    return normalized[:48] or "artifact"


def _graph_identifier(prefix: str, canonical_smiles: str) -> str:
    digest = hashlib.sha256(canonical_smiles.encode("utf-8")).hexdigest()[:32]
    return f"{prefix}-{digest}"


def _clear_atom_maps(molecule: object) -> object:
    Chem, _, _ = _rdkit_modules()
    copied = Chem.Mol(molecule)
    for atom in copied.GetAtoms():
        atom.SetAtomMapNum(0)
    return copied


def _ensure_atom_maps(molecule: object) -> None:
    used: set[int] = set()
    next_map = 1
    for atom in molecule.GetAtoms():
        atom_map = int(atom.GetAtomMapNum())
        if atom_map <= 0 or atom_map in used:
            while next_map in used:
                next_map += 1
            atom_map = next_map
            atom.SetAtomMapNum(atom_map)
        used.add(atom_map)
        next_map = max(next_map, atom_map + 1)


def _canonical_smiles(molecule: object) -> str:
    Chem, _, _ = _rdkit_modules()
    return str(
        Chem.MolToSmiles(
            _clear_atom_maps(molecule),
            canonical=True,
            isomericSmiles=True,
        )
    )


def _mapped_smiles(molecule: object) -> str:
    Chem, _, _ = _rdkit_modules()
    _ensure_atom_maps(molecule)
    return str(
        Chem.MolToSmiles(
            molecule,
            canonical=False,
            isomericSmiles=True,
            allHsExplicit=True,
        )
    )


def _component_count(molecule: object) -> int:
    Chem, _, _ = _rdkit_modules()
    return len(Chem.GetMolFrags(molecule, asMols=False, sanitizeFrags=False))


def _graph_from_molecule(
    molecule: object,
    *,
    representation_source: str,
    explicit_hydrogens: bool,
    source_atom_maps: Mapping[int, int] | None = None,
) -> MolecularGraph:
    _ensure_atom_maps(molecule)
    canonical_smiles = _canonical_smiles(molecule)
    mapped_smiles = _mapped_smiles(molecule)

    atoms = tuple(
        MolecularGraphAtom(
            atom_index=int(atom.GetIdx()),
            atomic_number=int(atom.GetAtomicNum()),
            element_symbol=str(atom.GetSymbol()),
            formal_charge=int(atom.GetFormalCharge()),
            isotope=int(atom.GetIsotope()),
            aromatic=bool(atom.GetIsAromatic()),
            chiral_tag=str(atom.GetChiralTag()).lower(),
            explicit_hydrogen_count=int(atom.GetNumExplicitHs()),
            implicit_hydrogen_count=int(atom.GetNumImplicitHs()),
            atom_map_number=int(atom.GetAtomMapNum()),
            source_atom_index=(
                source_atom_maps.get(int(atom.GetAtomMapNum()))
                if source_atom_maps is not None
                else None
            ),
        )
        for atom in molecule.GetAtoms()
    )
    bonds = tuple(
        MolecularGraphBond(
            bond_index=int(bond.GetIdx()),
            atom_index_a=int(bond.GetBeginAtomIdx()),
            atom_index_b=int(bond.GetEndAtomIdx()),
            bond_order=float(bond.GetBondTypeAsDouble()),
            bond_type=str(bond.GetBondType()).lower(),
            aromatic=bool(bond.GetIsAromatic()),
            conjugated=bool(bond.GetIsConjugated()),
            stereo=str(bond.GetStereo()).lower(),
        )
        for bond in molecule.GetBonds()
    )
    graph = MolecularGraph(
        schema_version=_SCHEMA_VERSION,
        graph_identifier=_graph_identifier(
            f"molecular-graph-{_safe_identifier_part(representation_source)}",
            mapped_smiles,
        ),
        representation_source=representation_source,
        canonical_smiles=canonical_smiles,
        mapped_smiles=mapped_smiles,
        atoms=atoms,
        bonds=bonds,
        sanitized=True,
        explicit_hydrogens=explicit_hydrogens,
        formal_charge=sum(atom.formal_charge for atom in atoms),
        component_count=_component_count(molecule),
    )
    if len(graph.atoms) > _MAXIMUM_ATOMS:
        raise ValueError("Molecular graphs cannot exceed the configured atom limit.")
    return graph


def _molecule_from_graph(graph: MolecularGraph) -> object:
    Chem, _, _ = _rdkit_modules()
    parameters = Chem.SmilesParserParams()
    parameters.sanitize = True
    parameters.removeHs = False
    molecule = Chem.MolFromSmiles(graph.mapped_smiles, parameters)
    if molecule is None:
        raise ValueError("The molecular graph exchange representation is invalid.")

    map_to_index: dict[int, int] = {}
    for atom in molecule.GetAtoms():
        atom_map = int(atom.GetAtomMapNum())
        if atom_map <= 0 or atom_map in map_to_index:
            raise ValueError("The molecular graph atom mapping is invalid.")
        map_to_index[atom_map] = int(atom.GetIdx())

    expected_maps = tuple(atom.atom_map_number for atom in graph.atoms)
    if set(expected_maps) != set(map_to_index):
        raise ValueError("The molecular graph atom mapping is incomplete.")

    order = [map_to_index[atom_map] for atom_map in expected_maps]
    molecule = Chem.RenumberAtoms(molecule, order)
    if int(molecule.GetNumAtoms()) != len(graph.atoms):
        raise ValueError(
            "The molecular graph atom count changed during reconstruction."
        )
    return molecule


class RDKitCheminformaticsAdapter:
    """Execute Phase 5.2 molecular capabilities through a pinned RDKit runtime."""

    def __init__(
        self,
        payload_store: MolecularArtifactPayloadStore,
        *,
        maximum_payload_bytes: int = _MAXIMUM_PAYLOAD_BYTES,
    ) -> None:
        if not isinstance(payload_store, MolecularArtifactPayloadStore):
            raise RDKitAdapterConfigurationError(
                "The RDKit adapter requires a molecular artifact payload store."
            )
        if maximum_payload_bytes <= 0:
            raise RDKitAdapterConfigurationError(
                "The maximum molecular payload size must be positive."
            )
        self._payload_store = payload_store
        self._maximum_payload_bytes = maximum_payload_bytes
        self._rdkit_version = _safe_rdkit_version()
        self._declaration = ScientificEngineAdapterDeclaration(
            adapter_identifier="adapter.rdkit.cheminformatics",
            adapter_version=_ADAPTER_VERSION,
            engine=ScientificEngineIdentity(
                engine_identifier="engine.rdkit",
                engine_version=self._rdkit_version or "unavailable",
            ),
            capabilities=rdkit_capability_envelopes(),
            metadata={
                "scientific_domain": "cheminformatics",
                "native_objects_public": False,
            },
        )
        self._envelopes = {
            envelope.descriptor.capability_name: envelope
            for envelope in self._declaration.capabilities
        }

    @property
    def declaration(self) -> ScientificEngineAdapterDeclaration:
        """Return the frozen adapter and capability declaration."""

        return self._declaration

    def health(self) -> ScientificEngineHealthReport:
        """Report dependency presence and pinned-version consistency."""

        current = _safe_rdkit_version()
        if current is None:
            return ScientificEngineHealthReport(
                status=HealthStatus.UNAVAILABLE,
                reason_code="dependency_missing",
                message="The optional RDKit dependency is unavailable.",
                diagnostics={"expected_version": _EXPECTED_RDKIT_VERSION},
            )
        if _normalized_version(current) != _normalized_version(_EXPECTED_RDKIT_VERSION):
            return ScientificEngineHealthReport(
                status=HealthStatus.DEGRADED,
                reason_code="dependency_version_mismatch",
                message="The installed RDKit version does not match the repository pin.",
                diagnostics={
                    "expected_version": _EXPECTED_RDKIT_VERSION,
                    "installed_version": current,
                },
            )
        return ScientificEngineHealthReport(
            status=HealthStatus.HEALTHY,
            diagnostics={"installed_version": current},
        )

    def invoke(self, invocation: CapabilityInvocation) -> CapabilityResult:
        """Execute one declared molecular capability with controlled failures."""

        if not isinstance(invocation, CapabilityInvocation):
            return self._failure(
                "invalid_invocation",
                "The RDKit adapter requires a scientific capability invocation.",
            )

        capability_name = invocation.capability.capability_name
        envelope = self._envelopes.get(capability_name)
        if envelope is None or invocation.capability != envelope.descriptor:
            return self._failure(
                "capability_not_declared",
                "The requested capability is not declared by this RDKit adapter.",
            )

        health = self.health()
        if health.status is HealthStatus.UNAVAILABLE:
            return self._failure(
                health.reason_code or "dependency_missing",
                health.message or "The RDKit dependency is unavailable.",
            )

        try:
            if capability_name == STRUCTURE_INGESTION:
                return self._ingest_structure(invocation)
            if capability_name == SMILES_PARSE:
                return self._parse_smiles(invocation)
            if capability_name == PREPARE:
                return self._prepare(invocation)
            if capability_name == CONFORMER_GENERATION:
                return self._generate_conformers(invocation)
            if capability_name == GEOMETRY_VALIDATION:
                return self._validate_geometry(invocation)
            if capability_name == FRAGMENT:
                return self._fragment(invocation)
            if capability_name == SCAFFOLD:
                return self._scaffold(invocation)
        except (ValidationError, ValueError, StructureIngestionError):
            return self._failure(
                "molecular_input_invalid",
                "The molecular capability input failed controlled validation.",
            )
        except ImportError:
            return self._failure(
                "dependency_missing",
                "The optional RDKit dependency is unavailable.",
            )
        except Exception:
            return self._failure(
                "molecular_execution_failed",
                "The molecular capability failed without producing valid evidence.",
            )

        return self._failure(
            "capability_not_implemented",
            "The requested molecular capability is not implemented.",
        )

    def _read_payload(self, reference: ArtifactReference) -> bytes:
        payload = self._payload_store.read(reference)
        if not isinstance(payload, bytes):
            raise ValueError("Artifact payload stores must return bytes.")
        if not payload or len(payload) > self._maximum_payload_bytes:
            raise ValueError("Molecular artifact payload size is invalid.")
        if hashlib.sha256(payload).hexdigest() != reference.content_sha256:
            raise ValueError("Molecular artifact bytes do not match their SHA-256.")
        if reference.byte_size is not None and reference.byte_size != len(payload):
            raise ValueError(
                "Molecular artifact bytes do not match their declared size."
            )
        return payload

    def _write_artifact(
        self,
        *,
        invocation: CapabilityInvocation,
        artifact_type: str,
        media_type: str,
        payload: bytes,
        identifier_prefix: str,
        parents: tuple[ArtifactReference, ...],
        metadata: Mapping[str, str | int | float | bool],
    ) -> ArtifactReference:
        if not payload or len(payload) > self._maximum_payload_bytes:
            raise ValueError("Generated molecular artifact payload size is invalid.")
        digest = hashlib.sha256(payload).hexdigest()
        identity_digest = hashlib.sha256(
            (
                f"{invocation.capability.capability_name}:"
                f"{invocation.context.execution_id}:{digest}"
            ).encode("utf-8")
        ).hexdigest()
        reference = ArtifactReference(
            artifact_identifier=f"{identifier_prefix}-{identity_digest[:32]}",
            schema_version=_SCHEMA_VERSION,
            artifact_type=artifact_type,
            media_type=media_type,
            content_sha256=digest,
            byte_size=len(payload),
            storage_location=None,
            metadata=dict(metadata),
            provenance=CreationProvenance(
                producer=invocation.capability.capability_name,
                producer_version=invocation.capability.version,
                execution_identifier=invocation.context.execution_id,
            ),
            parents=tuple(parent.pointer for parent in parents),
        )
        self._payload_store.write(reference, payload)
        return reference

    def _success(
        self,
        invocation: CapabilityInvocation,
        *,
        artifacts: tuple[ArtifactReference, ...],
        lineage: tuple[ArtifactLineageEdge, ...] = (),
        diagnostics: Mapping[str, str | int | float | bool] | None = None,
    ) -> CapabilityResult:
        return CapabilityResult(
            status=ExecutionStatus.SUCCESS,
            output_artifacts=artifacts,
            lineage=lineage,
            diagnostics=dict(diagnostics or {}),
            execution_evidence=ExecutionEvidence(
                runtime_identifier="rdkit.local",
                exit_code=0,
                evidence_artifacts=tuple(artifact.pointer for artifact in artifacts),
                details={
                    "capability_name": invocation.capability.capability_name,
                    "rdkit_version": self._rdkit_version or "unknown",
                },
            ),
        )

    @staticmethod
    def _failure(
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> CapabilityResult:
        return CapabilityResult(
            status=ExecutionStatus.FAILED,
            failure=FailureInformation(
                code=code,
                message=message,
                retryable=retryable,
            ),
        )

    @staticmethod
    def _one_input(
        invocation: CapabilityInvocation,
        accepted_types: tuple[str, ...],
    ) -> ArtifactReference:
        if len(invocation.input_artifacts) != 1:
            raise ValueError(
                "The molecular capability requires exactly one input artifact."
            )
        reference = invocation.input_artifacts[0]
        if reference.artifact_type not in accepted_types:
            raise ValueError("The molecular capability input artifact type is invalid.")
        return reference

    @staticmethod
    def _parameter(
        invocation: CapabilityInvocation,
        name: str,
        *,
        default: object = None,
        required: bool = False,
    ) -> object:
        parameters = dict(invocation.parameters)
        if name not in parameters:
            if required:
                raise ValueError(f"Missing required molecular parameter: {name}.")
            return default
        return parameters[name]

    @classmethod
    def _string_parameter(
        cls,
        invocation: CapabilityInvocation,
        name: str,
        *,
        default: str | None = None,
        required: bool = False,
        maximum_length: int = 4096,
    ) -> str:
        value = cls._parameter(
            invocation,
            name,
            default=default,
            required=required,
        )
        if not isinstance(value, str):
            raise ValueError(f"Molecular parameter {name} must be text.")
        normalized = value.strip()
        if not normalized or len(normalized) > maximum_length:
            raise ValueError(f"Molecular parameter {name} is invalid.")
        return normalized

    @classmethod
    def _integer_parameter(
        cls,
        invocation: CapabilityInvocation,
        name: str,
        *,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        value = cls._parameter(invocation, name, default=default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"Molecular parameter {name} must be an integer.")
        if value < minimum or value > maximum:
            raise ValueError(
                f"Molecular parameter {name} is outside its allowed range."
            )
        return value

    @classmethod
    def _float_parameter(
        cls,
        invocation: CapabilityInvocation,
        name: str,
        *,
        default: float,
        minimum: float,
        maximum: float,
    ) -> float:
        value = cls._parameter(invocation, name, default=default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Molecular parameter {name} must be numeric.")
        normalized = float(value)
        if not math.isfinite(normalized) or not minimum <= normalized <= maximum:
            raise ValueError(
                f"Molecular parameter {name} is outside its allowed range."
            )
        return normalized

    @classmethod
    def _boolean_parameter(
        cls,
        invocation: CapabilityInvocation,
        name: str,
        *,
        default: bool,
    ) -> bool:
        value = cls._parameter(invocation, name, default=default)
        if not isinstance(value, bool):
            raise ValueError(f"Molecular parameter {name} must be boolean.")
        return value

    def _load_graph(
        self,
        reference: ArtifactReference,
    ) -> MolecularGraph:
        payload = self._read_payload(reference)
        return MolecularGraph.model_validate_json(payload)

    def _load_conformer_set(
        self,
        reference: ArtifactReference,
    ) -> MolecularConformerSet:
        payload = self._read_payload(reference)
        return MolecularConformerSet.model_validate_json(payload)

    def _lineage(
        self,
        invocation: CapabilityInvocation,
        source: ArtifactReference,
        destination: ArtifactReference,
        relationship_type: str,
    ) -> ArtifactLineageEdge:
        return ArtifactLineageEdge(
            source=source.pointer,
            destination=destination.pointer,
            relationship_type=relationship_type,
            producing_capability=invocation.capability.capability_name,
            producing_capability_version=invocation.capability.version,
            execution_identifier=invocation.context.execution_id,
        )

    def _ingest_structure(self, invocation: CapabilityInvocation) -> CapabilityResult:
        source = self._one_input(invocation, ("molecular_structure",))
        source_bytes = self._read_payload(source)
        structure_format = self._string_parameter(
            invocation,
            "structure_format",
            required=True,
            maximum_length=16,
        ).lower()
        coordinate_unit = self._string_parameter(
            invocation,
            "coordinate_unit",
            default="angstrom",
            maximum_length=16,
        ).lower()
        structure_identifier = self._string_parameter(
            invocation,
            "structure_identifier",
            required=True,
            maximum_length=128,
        )
        system_identifier = self._string_parameter(
            invocation,
            "system_identifier",
            required=True,
            maximum_length=128,
        )
        provenance = CreationProvenance(
            producer=invocation.capability.capability_name,
            producer_version=invocation.capability.version,
            execution_identifier=invocation.context.execution_id,
        )
        result = ingest_structure_bytes(
            source_bytes=source_bytes,
            source_reference=source,
            structure_identifier=structure_identifier,
            system_identifier=system_identifier,
            structure_format=structure_format,
            coordinate_unit=coordinate_unit,
            provenance=provenance,
        )
        self._payload_store.write(result.topology_artifact, result.topology_payload)
        return self._success(
            invocation,
            artifacts=(result.topology_artifact,),
            lineage=(result.lineage_edge,),
            diagnostics={
                "atom_count": result.structure.atom_count,
                "bond_count": result.structure.bond_count,
                "parser_identifier": result.topology.parser_identifier,
                "parser_version": result.topology.parser_version,
            },
        )

    def _parse_smiles(self, invocation: CapabilityInvocation) -> CapabilityResult:
        if invocation.input_artifacts:
            raise ValueError("SMILES parsing does not accept input artifacts.")
        smiles = self._string_parameter(
            invocation,
            "smiles",
            required=True,
            maximum_length=_MAXIMUM_SMILES_LENGTH,
        )
        Chem, _, _ = _rdkit_modules()
        molecule = Chem.MolFromSmiles(smiles, sanitize=True)
        if molecule is None or int(molecule.GetNumAtoms()) <= 0:
            raise ValueError("The supplied SMILES string is invalid.")
        for atom in molecule.GetAtoms():
            atom.SetAtomMapNum(int(atom.GetIdx()) + 1)
        graph = _graph_from_molecule(
            molecule,
            representation_source="smiles",
            explicit_hydrogens=False,
            source_atom_maps={
                int(atom.GetAtomMapNum()): int(atom.GetIdx())
                for atom in molecule.GetAtoms()
            },
        )
        payload = graph.to_canonical_json().encode("utf-8")
        artifact = self._write_artifact(
            invocation=invocation,
            artifact_type="molecular_graph",
            media_type="application/vnd.pulsate.molecular-graph+json",
            payload=payload,
            identifier_prefix="molecular-graph",
            parents=(),
            metadata={
                "atom_count": len(graph.atoms),
                "bond_count": len(graph.bonds),
                "component_count": graph.component_count,
                "formal_charge": graph.formal_charge,
            },
        )
        return self._success(
            invocation,
            artifacts=(artifact,),
            diagnostics={
                "atom_count": len(graph.atoms),
                "bond_count": len(graph.bonds),
                "component_count": graph.component_count,
            },
        )

    def _prepare(self, invocation: CapabilityInvocation) -> CapabilityResult:
        source = self._one_input(invocation, ("molecular_graph",))
        graph = self._load_graph(source)
        molecule = _molecule_from_graph(graph)
        Chem, _, _ = _rdkit_modules()
        Chem.SanitizeMol(molecule)
        Chem.AssignStereochemistry(molecule, cleanIt=True, force=True)
        source_atom_maps = {
            int(atom.GetAtomMapNum()): int(atom.GetIdx())
            for atom in molecule.GetAtoms()
        }
        add_hydrogens = self._boolean_parameter(
            invocation,
            "add_hydrogens",
            default=True,
        )
        if add_hydrogens:
            molecule = Chem.AddHs(molecule, addCoords=False)
        _ensure_atom_maps(molecule)
        prepared = _graph_from_molecule(
            molecule,
            representation_source="prepared_graph",
            explicit_hydrogens=add_hydrogens,
            source_atom_maps=source_atom_maps,
        )
        payload = prepared.to_canonical_json().encode("utf-8")
        artifact = self._write_artifact(
            invocation=invocation,
            artifact_type="prepared_molecular_graph",
            media_type="application/vnd.pulsate.molecular-graph+json",
            payload=payload,
            identifier_prefix="prepared-molecular-graph",
            parents=(source,),
            metadata={
                "atom_count": len(prepared.atoms),
                "bond_count": len(prepared.bonds),
                "explicit_hydrogens": prepared.explicit_hydrogens,
                "formal_charge": prepared.formal_charge,
            },
        )
        return self._success(
            invocation,
            artifacts=(artifact,),
            lineage=(self._lineage(invocation, source, artifact, "prepared_from"),),
            diagnostics={
                "source_atom_count": len(graph.atoms),
                "prepared_atom_count": len(prepared.atoms),
                "formal_charge": prepared.formal_charge,
            },
        )

    def _generate_conformers(
        self,
        invocation: CapabilityInvocation,
    ) -> CapabilityResult:
        source = self._one_input(invocation, ("prepared_molecular_graph",))
        graph = self._load_graph(source)
        molecule = _molecule_from_graph(graph)
        _, AllChem, _ = _rdkit_modules()

        conformer_count = self._integer_parameter(
            invocation,
            "conformer_count",
            default=10,
            minimum=1,
            maximum=_MAXIMUM_CONFORMERS,
        )
        random_seed = self._integer_parameter(
            invocation,
            "random_seed",
            default=7,
            minimum=0,
            maximum=2_147_483_647,
        )
        maximum_iterations = self._integer_parameter(
            invocation,
            "maximum_iterations",
            default=500,
            minimum=1,
            maximum=10_000,
        )
        prune_rms_threshold = self._float_parameter(
            invocation,
            "prune_rms_threshold",
            default=0.1,
            minimum=-1.0,
            maximum=100.0,
        )
        optimize = self._boolean_parameter(
            invocation,
            "optimize",
            default=True,
        )

        embed = AllChem.ETKDGv3()
        embed.randomSeed = random_seed
        embed.numThreads = 1
        embed.pruneRmsThresh = prune_rms_threshold
        conformer_ids = tuple(
            int(identifier)
            for identifier in AllChem.EmbedMultipleConfs(
                molecule,
                numConfs=conformer_count,
                params=embed,
            )
        )
        if not conformer_ids:
            return self._failure(
                "conformer_generation_failed",
                "RDKit could not generate a molecular conformer.",
            )

        force_field = "none"
        optimization: dict[int, tuple[int, float | None]] = {
            conformer_id: (0, None) for conformer_id in conformer_ids
        }
        if optimize:
            if AllChem.MMFFHasAllMoleculeParams(molecule):
                properties = AllChem.MMFFGetMoleculeProperties(
                    molecule,
                    mmffVariant="MMFF94s",
                )
                force_field = "mmff94s"
                for conformer_id in conformer_ids:
                    field = AllChem.MMFFGetMoleculeForceField(
                        molecule,
                        properties,
                        confId=conformer_id,
                    )
                    if field is None:
                        raise ValueError("MMFF force-field construction failed.")
                    status = int(field.Minimize(maxIts=maximum_iterations))
                    optimization[conformer_id] = (status, float(field.CalcEnergy()))
            elif AllChem.UFFHasAllMoleculeParams(molecule):
                force_field = "uff"
                for conformer_id in conformer_ids:
                    field = AllChem.UFFGetMoleculeForceField(
                        molecule,
                        confId=conformer_id,
                    )
                    if field is None:
                        raise ValueError("UFF force-field construction failed.")
                    status = int(field.Minimize(maxIts=maximum_iterations))
                    optimization[conformer_id] = (status, float(field.CalcEnergy()))

        ordered_ids = tuple(
            sorted(
                conformer_ids,
                key=lambda identifier: (
                    optimization[identifier][1]
                    if optimization[identifier][1] is not None
                    else math.inf,
                    identifier,
                ),
            )
        )
        conformers: list[MolecularConformer] = []
        for output_index, conformer_id in enumerate(ordered_ids):
            conformer = molecule.GetConformer(conformer_id)
            status, energy = optimization[conformer_id]
            coordinates = tuple(
                MolecularCoordinate(
                    atom_index=atom_index,
                    x=_rounded(conformer.GetAtomPosition(atom_index).x),
                    y=_rounded(conformer.GetAtomPosition(atom_index).y),
                    z=_rounded(conformer.GetAtomPosition(atom_index).z),
                )
                for atom_index in range(int(molecule.GetNumAtoms()))
            )
            conformers.append(
                MolecularConformer(
                    conformer_identifier=(
                        f"conformer-{output_index}-{hashlib.sha256(str(coordinates).encode('utf-8')).hexdigest()[:20]}"
                    ),
                    conformer_index=output_index,
                    coordinates=coordinates,
                    energy=_rounded(energy) if energy is not None else None,
                    energy_unit=(
                        "kilocalorie_per_mole" if energy is not None else None
                    ),
                    optimization_status=(
                        "not_requested"
                        if not optimize or force_field == "none"
                        else "converged"
                        if status == 0
                        else "not_converged"
                    ),
                )
            )

        conformer_set = MolecularConformerSet(
            schema_version=_SCHEMA_VERSION,
            conformer_set_identifier=(
                "molecular-conformer-set-"
                + hashlib.sha256(
                    (
                        graph.fingerprint
                        + f":{random_seed}:{conformer_count}:{force_field}"
                    ).encode("utf-8")
                ).hexdigest()[:32]
            ),
            source_graph=graph,
            generation_method="rdkit_etkdg_v3",
            force_field=force_field,
            random_seed=random_seed,
            requested_conformer_count=conformer_count,
            conformers=tuple(conformers),
        )
        payload = conformer_set.to_canonical_json().encode("utf-8")
        artifact = self._write_artifact(
            invocation=invocation,
            artifact_type="molecular_conformer_set",
            media_type="application/vnd.pulsate.molecular-conformer-set+json",
            payload=payload,
            identifier_prefix="molecular-conformer-set",
            parents=(source,),
            metadata={
                "atom_count": len(graph.atoms),
                "conformer_count": len(conformer_set.conformers),
                "force_field": force_field,
                "random_seed": random_seed,
            },
        )
        return self._success(
            invocation,
            artifacts=(artifact,),
            lineage=(self._lineage(invocation, source, artifact, "generated_from"),),
            diagnostics={
                "generated_conformer_count": len(conformer_set.conformers),
                "force_field": force_field,
                "random_seed": random_seed,
            },
        )

    def _validate_geometry(
        self,
        invocation: CapabilityInvocation,
    ) -> CapabilityResult:
        source = self._one_input(invocation, ("molecular_conformer_set",))
        conformer_set = self._load_conformer_set(source)
        minimum_distance = self._float_parameter(
            invocation,
            "minimum_interatomic_distance",
            default=0.4,
            minimum=0.05,
            maximum=5.0,
        )
        minimum_bond_ratio = self._float_parameter(
            invocation,
            "minimum_bond_ratio",
            default=0.55,
            minimum=0.05,
            maximum=1.0,
        )
        maximum_bond_ratio = self._float_parameter(
            invocation,
            "maximum_bond_ratio",
            default=1.8,
            minimum=1.0,
            maximum=10.0,
        )
        if minimum_bond_ratio >= maximum_bond_ratio:
            raise ValueError("Geometry bond-ratio bounds are invalid.")

        Chem, _, _ = _rdkit_modules()
        periodic_table = Chem.GetPeriodicTable()
        graph = conformer_set.source_graph
        bonded = {(bond.atom_index_a, bond.atom_index_b): bond for bond in graph.bonds}
        findings: list[MolecularGeometryFinding] = []

        for conformer in conformer_set.conformers:
            positions = {
                coordinate.atom_index: (
                    coordinate.x,
                    coordinate.y,
                    coordinate.z,
                )
                for coordinate in conformer.coordinates
            }
            for atom_a in range(len(graph.atoms)):
                for atom_b in range(atom_a + 1, len(graph.atoms)):
                    left = positions[atom_a]
                    right = positions[atom_b]
                    distance = math.dist(left, right)
                    bond = bonded.get((atom_a, atom_b))
                    if distance < minimum_distance:
                        findings.append(
                            MolecularGeometryFinding(
                                finding_code="atom_clash",
                                severity="error",
                                message="Two atoms are separated by an implausibly short distance.",
                                conformer_index=conformer.conformer_index,
                                atom_indices=(atom_a, atom_b),
                                observed_value=_rounded(distance),
                                expected_minimum=minimum_distance,
                                unit="angstrom",
                            )
                        )
                    if bond is None:
                        continue
                    atom_record_a = graph.atoms[atom_a]
                    atom_record_b = graph.atoms[atom_b]
                    expected = float(
                        periodic_table.GetRcovalent(atom_record_a.atomic_number)
                    ) + float(periodic_table.GetRcovalent(atom_record_b.atomic_number))
                    lower = expected * minimum_bond_ratio
                    upper = expected * maximum_bond_ratio
                    if not lower <= distance <= upper:
                        findings.append(
                            MolecularGeometryFinding(
                                finding_code="bond_length_implausible",
                                severity="error",
                                message="A bonded atom pair has an implausible distance.",
                                conformer_index=conformer.conformer_index,
                                atom_indices=(atom_a, atom_b),
                                observed_value=_rounded(distance),
                                expected_minimum=_rounded(lower),
                                expected_maximum=_rounded(upper),
                                unit="angstrom",
                            )
                        )

        validation = MolecularGeometryValidation(
            schema_version=_SCHEMA_VERSION,
            validation_identifier=(
                "molecular-geometry-validation-"
                + hashlib.sha256(
                    (
                        conformer_set.fingerprint
                        + f":{minimum_distance}:{minimum_bond_ratio}:{maximum_bond_ratio}"
                    ).encode("utf-8")
                ).hexdigest()[:32]
            ),
            conformer_set_identifier=conformer_set.conformer_set_identifier,
            valid=not any(finding.severity == "error" for finding in findings),
            evaluated_conformer_count=len(conformer_set.conformers),
            atom_count=len(graph.atoms),
            findings=tuple(findings),
        )
        payload = validation.to_canonical_json().encode("utf-8")
        artifact = self._write_artifact(
            invocation=invocation,
            artifact_type="molecular_geometry_validation",
            media_type="application/vnd.pulsate.molecular-geometry-validation+json",
            payload=payload,
            identifier_prefix="molecular-geometry-validation",
            parents=(source,),
            metadata={
                "valid": validation.valid,
                "finding_count": len(validation.findings),
                "conformer_count": validation.evaluated_conformer_count,
            },
        )
        return self._success(
            invocation,
            artifacts=(artifact,),
            lineage=(self._lineage(invocation, source, artifact, "validated_by"),),
            diagnostics={
                "valid": validation.valid,
                "finding_count": len(validation.findings),
            },
        )

    def _fragment(self, invocation: CapabilityInvocation) -> CapabilityResult:
        source = self._one_input(
            invocation,
            ("molecular_graph", "prepared_molecular_graph"),
        )
        graph = self._load_graph(source)
        molecule = _molecule_from_graph(graph)
        Chem, _, _ = _rdkit_modules()
        mappings: list[tuple[int, ...]] = []
        fragments = Chem.GetMolFrags(
            molecule,
            asMols=True,
            sanitizeFrags=True,
            fragsMolAtomMapping=mappings,
        )
        records: list[tuple[tuple[int, ...], str, int, int]] = []
        for fragment, atom_indices in zip(fragments, mappings, strict=True):
            canonical_smiles = _canonical_smiles(fragment)
            formal_charge = sum(
                int(atom.GetFormalCharge()) for atom in fragment.GetAtoms()
            )
            records.append(
                (
                    tuple(sorted(int(index) for index in atom_indices)),
                    canonical_smiles,
                    formal_charge,
                    int(fragment.GetNumHeavyAtoms()),
                )
            )
        records.sort(key=lambda record: (record[0], record[1]))
        fragment_models = tuple(
            MolecularFragment(
                fragment_identifier=(
                    f"fragment-{index}-{hashlib.sha256((record[1] + str(record[0])).encode('utf-8')).hexdigest()[:20]}"
                ),
                fragment_index=index,
                source_atom_indices=record[0],
                canonical_smiles=record[1],
                formal_charge=record[2],
                heavy_atom_count=record[3],
            )
            for index, record in enumerate(records)
        )
        fragment_set = MolecularFragmentSet(
            schema_version=_SCHEMA_VERSION,
            fragment_set_identifier=(
                "molecular-fragment-set-"
                + hashlib.sha256(graph.fingerprint.encode("utf-8")).hexdigest()[:32]
            ),
            source_graph_identifier=graph.graph_identifier,
            fragments=fragment_models,
        )
        payload = fragment_set.to_canonical_json().encode("utf-8")
        artifact = self._write_artifact(
            invocation=invocation,
            artifact_type="molecular_fragment_set",
            media_type="application/vnd.pulsate.molecular-fragment-set+json",
            payload=payload,
            identifier_prefix="molecular-fragment-set",
            parents=(source,),
            metadata={"fragment_count": len(fragment_set.fragments)},
        )
        return self._success(
            invocation,
            artifacts=(artifact,),
            lineage=(self._lineage(invocation, source, artifact, "fragmented_into"),),
            diagnostics={"fragment_count": len(fragment_set.fragments)},
        )

    def _scaffold(self, invocation: CapabilityInvocation) -> CapabilityResult:
        source = self._one_input(
            invocation,
            ("molecular_graph", "prepared_molecular_graph"),
        )
        graph = self._load_graph(source)
        molecule = _molecule_from_graph(graph)
        _, _, MurckoScaffold = _rdkit_modules()
        scaffold_molecule = MurckoScaffold.GetScaffoldForMol(molecule)
        atom_count = int(scaffold_molecule.GetNumAtoms())
        if atom_count == 0:
            canonical_smiles = None
            source_atom_indices: tuple[int, ...] = ()
        else:
            canonical_smiles = _canonical_smiles(scaffold_molecule)
            source_atom_indices = tuple(
                int(index) for index in molecule.GetSubstructMatch(scaffold_molecule)
            )
            if len(source_atom_indices) != atom_count:
                raise ValueError(
                    "The molecular scaffold could not be mapped to source atoms."
                )
        scaffold = MolecularScaffold(
            schema_version=_SCHEMA_VERSION,
            scaffold_identifier=(
                "molecular-scaffold-"
                + hashlib.sha256(
                    (graph.fingerprint + ":bemis_murcko").encode("utf-8")
                ).hexdigest()[:32]
            ),
            source_graph_identifier=graph.graph_identifier,
            canonical_smiles=canonical_smiles,
            source_atom_indices=source_atom_indices,
            heavy_atom_count=int(scaffold_molecule.GetNumHeavyAtoms()),
            empty=atom_count == 0,
        )
        payload = scaffold.to_canonical_json().encode("utf-8")
        artifact = self._write_artifact(
            invocation=invocation,
            artifact_type="molecular_scaffold",
            media_type="application/vnd.pulsate.molecular-scaffold+json",
            payload=payload,
            identifier_prefix="molecular-scaffold",
            parents=(source,),
            metadata={
                "empty": scaffold.empty,
                "heavy_atom_count": scaffold.heavy_atom_count,
            },
        )
        return self._success(
            invocation,
            artifacts=(artifact,),
            lineage=(self._lineage(invocation, source, artifact, "derived_scaffold"),),
            diagnostics={
                "empty": scaffold.empty,
                "heavy_atom_count": scaffold.heavy_atom_count,
            },
        )
