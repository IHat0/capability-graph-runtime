"""Concrete RDKit molecular components for the Phase 7 discovery runtime."""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, runtime_checkable

from cgr.kernel.contracts import CapabilityVersion
from cgr.science import ArtifactPointer
from cgr.science.canonical import validate_identifier
from cgr.scientific_verification import (
    VERIFICATION_DIMENSION_ORDER,
    DimensionVerificationResult,
    ScientificVerificationFinding,
    ScientificVerificationReport,
    ScientificVerificationSeverity,
    VerificationDimension,
    VerificationOutcome,
)

from .contracts import (
    CandidateAssessment,
    CandidateConstraintResult,
    CandidateEvaluationResult,
    CandidateEvidenceRecord,
    CandidateGenerationRequest,
    CandidateGenerationResult,
    CandidateObjectiveScore,
    CandidateTransformation,
    CandidateValidityResult,
    DiscoveryCandidate,
)

_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
_CANDIDATE_TYPE = "small_molecule"
_GENERATOR = "generator.rdkit_scaffold_transform"
_DE_NOVO_GENERATOR = "generator.rdkit_de_novo_fragment_assembly"
_CHECKER = "checker.rdkit_druglike_validity"
_EVALUATOR = "evaluator.rdkit_3d_descriptors"
_VERIFIER = "verifier.molecular_candidate_evidence"
_DOCKING_EVALUATOR = "evaluator.autodock_vina_pose"


def _identifier(prefix: str, *values: object) -> str:
    encoded = "\x1f".join(str(value) for value in values).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:32]}"


@runtime_checkable
class MolecularCandidateArtifactStore(Protocol):
    """Content-addressed representation/evidence storage used by components."""

    def put(self, artifact_type: str, payload: bytes) -> ArtifactPointer:
        """Persist exact bytes and return their immutable pointer."""
        ...

    def read(self, pointer: ArtifactPointer) -> bytes:
        """Read exact bytes matching an immutable pointer."""
        ...


def _modules() -> tuple[object, object, object, object, object]:
    from rdkit import Chem
    from rdkit.Chem import QED, AllChem, Crippen, Descriptors, Lipinski

    return Chem, AllChem, Crippen, Descriptors, (Lipinski, QED)


def _smiles_payload(smiles: str) -> bytes:
    return json.dumps(
        {"canonical_smiles": smiles, "representation": "rdkit_canonical_smiles"},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _read_smiles(store: MolecularCandidateArtifactStore, candidate: DiscoveryCandidate) -> str:
    payload = store.read(candidate.representation_artifacts[0])
    if hashlib.sha256(payload).hexdigest() != (
        candidate.representation_artifacts[0].content_sha256
    ):
        raise ValueError("Candidate representation bytes fail content addressing.")
    decoded = json.loads(payload)
    smiles = decoded.get("canonical_smiles") if isinstance(decoded, dict) else None
    if not isinstance(smiles, str) or not smiles:
        raise ValueError("Candidate representation does not contain canonical SMILES.")
    return smiles


def _canonical_smiles(smiles: str) -> str:
    Chem, _, _, _, _ = _modules()
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise ValueError("Seed, fragment, or generated SMILES is invalid.")
    Chem.SanitizeMol(molecule)
    return str(Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True))


def _single_site_mutations(
    smiles: str,
    substituents: tuple[str, ...],
) -> tuple[tuple[str, str, int], ...]:
    Chem, _, _, _, _ = _modules()
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise ValueError("Parent SMILES is invalid.")
    generated: dict[str, tuple[str, int]] = {}
    for atom in molecule.GetAtoms():
        if atom.GetTotalNumHs() <= 0 or (
            atom.GetIsAromatic() and atom.GetSymbol() == "N"
        ):
            continue
        for symbol in substituents:
            editable = Chem.RWMol(molecule)
            new_index = editable.AddAtom(Chem.Atom(symbol))
            editable.AddBond(atom.GetIdx(), new_index, Chem.BondType.SINGLE)
            candidate = editable.GetMol()
            try:
                Chem.SanitizeMol(candidate)
            except Exception:
                continue
            canonical = str(
                Chem.MolToSmiles(
                    candidate,
                    canonical=True,
                    isomericSmiles=True,
                )
            )
            generated.setdefault(canonical, (symbol, int(atom.GetIdx())))
    return tuple(
        (canonical, symbol, atom_index)
        for canonical, (symbol, atom_index) in sorted(generated.items())
    )


class RDKitMolecularCandidateGenerator:
    """Bounded deterministic seed and single-site scaffold transformations."""

    def __init__(
        self,
        store: MolecularCandidateArtifactStore,
        *,
        seed_smiles: tuple[str, ...],
        allowed_substituents: tuple[str, ...] = ("C", "F", "Cl"),
    ) -> None:
        if not isinstance(store, MolecularCandidateArtifactStore):
            raise TypeError("Molecular generation requires an artifact store.")
        if not seed_smiles:
            raise ValueError("Molecular generation requires seed chemistry.")
        if not allowed_substituents or any(
            value not in {"C", "N", "O", "F", "Cl"}
            for value in allowed_substituents
        ):
            raise ValueError("Unsupported molecular substituent configuration.")
        self._store = store
        self._seed_smiles = seed_smiles
        self._substituents = allowed_substituents

    @property
    def generator_identifier(self) -> str:
        return _GENERATOR

    @property
    def supported_candidate_types(self) -> tuple[str, ...]:
        return (_CANDIDATE_TYPE,)

    @staticmethod
    def _canonical(smiles: str) -> str:
        return _canonical_smiles(smiles)

    def _mutations(self, smiles: str) -> tuple[tuple[str, str, int], ...]:
        return _single_site_mutations(smiles, self._substituents)

    def propose(self, request: CandidateGenerationRequest) -> CandidateGenerationResult:
        proposals: list[DiscoveryCandidate] = []
        if request.generation == 0:
            smiles_values = tuple(sorted({self._canonical(item) for item in self._seed_smiles}))
            for smiles in smiles_values[: request.max_candidates]:
                pointer = self._store.put("molecular_candidate_smiles", _smiles_payload(smiles))
                proposals.append(DiscoveryCandidate(
                    candidate_identifier=_identifier("candidate-molecule", 0, smiles),
                    candidate_type=_CANDIDATE_TYPE,
                    generation=0,
                    generated_by=self.generator_identifier,
                    representation_artifacts=(pointer,),
                    objective_identifiers=request.objective_identifiers,
                    metadata={"canonical_smiles": smiles, "generation_kind": "seed"},
                ))
        else:
            for parent in request.parent_candidates:
                parent_smiles = _read_smiles(self._store, parent)
                for smiles, substituent, atom_index in self._mutations(parent_smiles):
                    pointer = self._store.put(
                        "molecular_candidate_smiles", _smiles_payload(smiles)
                    )
                    transformation = CandidateTransformation(
                        transformation_identifier=_identifier(
                            "transformation-substitution",
                            parent.candidate_identifier,
                            smiles,
                        ),
                        transformation_kind="single_site_substitution",
                        source_candidate_identifiers=(parent.candidate_identifier,),
                        target_candidate_type=_CANDIDATE_TYPE,
                        generator_identifier=self.generator_identifier,
                        rationale=(
                            "A bounded valence-checked substituent was added at a "
                            "hydrogen-bearing scaffold atom."
                        ),
                        parameters={
                            "substituent": substituent,
                            "parent_atom_index": atom_index,
                        },
                    )
                    proposals.append(DiscoveryCandidate(
                        candidate_identifier=_identifier(
                            "candidate-molecule", request.generation, smiles
                        ),
                        candidate_type=_CANDIDATE_TYPE,
                        generation=request.generation,
                        generated_by=self.generator_identifier,
                        representation_artifacts=(pointer,),
                        objective_identifiers=request.objective_identifiers,
                        parent_candidate_identifiers=(parent.candidate_identifier,),
                        transformation=transformation,
                        metadata={
                            "canonical_smiles": smiles,
                            "generation_kind": "scaffold_substitution",
                        },
                    ))
                    if len(proposals) >= request.max_candidates:
                        break
                if len(proposals) >= request.max_candidates:
                    break
        return CandidateGenerationResult(
            result_identifier=_identifier(
                "generation-result", request.request_identifier, *(x.candidate_identifier for x in proposals)
            ),
            generator_identifier=self.generator_identifier,
            request_sha256=request.fingerprint,
            candidates=tuple(proposals),
        )


class RDKitDeNovoMolecularCandidateGenerator:
    """Deterministic seedless graph construction from declared fragment policy."""

    def __init__(
        self,
        store: MolecularCandidateArtifactStore,
        *,
        construction_fragments: tuple[str, ...],
        allowed_extensions: tuple[str, ...] = ("C", "N", "O", "F"),
        assembly_depth: int = 2,
        construction_policy_identifier: str = "policy.rdkit_de_novo_fragments_v1",
    ) -> None:
        if not isinstance(store, MolecularCandidateArtifactStore):
            raise TypeError("Molecular generation requires an artifact store.")
        if not construction_fragments:
            raise ValueError("De novo generation requires declared construction fragments.")
        if not allowed_extensions or any(
            value not in {"C", "N", "O", "F", "Cl"}
            for value in allowed_extensions
        ):
            raise ValueError("Unsupported de novo molecular extension configuration.")
        if not 1 <= assembly_depth <= 4:
            raise ValueError("De novo molecular assembly depth must be between one and four.")
        self._store = store
        self._fragments = tuple(
            sorted({_canonical_smiles(item) for item in construction_fragments})
        )
        self._extensions = allowed_extensions
        self._assembly_depth = assembly_depth
        self._policy = validate_identifier(
            construction_policy_identifier,
            label="de novo construction policy",
        )

    @property
    def generator_identifier(self) -> str:
        return _DE_NOVO_GENERATOR

    @property
    def supported_candidate_types(self) -> tuple[str, ...]:
        return (_CANDIDATE_TYPE,)

    def _initial_candidates(self) -> tuple[tuple[str, str, int], ...]:
        frontier: dict[str, tuple[str, int]] = {
            fragment: (fragment, 0) for fragment in self._fragments
        }
        generated: dict[str, tuple[str, int]] = {}
        for depth in range(1, self._assembly_depth + 1):
            following: dict[str, tuple[str, int]] = {}
            for parent, (root, _) in sorted(frontier.items()):
                for smiles, _, _ in _single_site_mutations(
                    parent,
                    self._extensions,
                ):
                    if smiles in self._fragments:
                        continue
                    generated.setdefault(smiles, (root, depth))
                    following.setdefault(smiles, (root, depth))
            frontier = following
            if not frontier:
                break
        return tuple(
            (smiles, root, depth)
            for smiles, (root, depth) in sorted(generated.items())
        )

    def propose(self, request: CandidateGenerationRequest) -> CandidateGenerationResult:
        proposals: list[DiscoveryCandidate] = []
        if request.generation == 0:
            for smiles, fragment, depth in self._initial_candidates()[
                : request.max_candidates
            ]:
                pointer = self._store.put(
                    "molecular_candidate_smiles",
                    _smiles_payload(smiles),
                )
                proposals.append(
                    DiscoveryCandidate(
                        candidate_identifier=_identifier(
                            "candidate-molecule",
                            request.generation,
                            smiles,
                        ),
                        candidate_type=_CANDIDATE_TYPE,
                        generation=0,
                        generated_by=self.generator_identifier,
                        representation_artifacts=(pointer,),
                        objective_identifiers=request.objective_identifiers,
                        metadata={
                            "canonical_smiles": smiles,
                            "generation_kind": "de_novo_fragment_assembly",
                            "construction_policy_identifier": self._policy,
                            "construction_fragment": fragment,
                            "construction_step_count": depth,
                        },
                    )
                )
        else:
            for parent in request.parent_candidates:
                parent_smiles = _read_smiles(self._store, parent)
                for smiles, extension, atom_index in _single_site_mutations(
                    parent_smiles,
                    self._extensions,
                ):
                    pointer = self._store.put(
                        "molecular_candidate_smiles",
                        _smiles_payload(smiles),
                    )
                    transformation = CandidateTransformation(
                        transformation_identifier=_identifier(
                            "transformation-de-novo-extension",
                            parent.candidate_identifier,
                            smiles,
                        ),
                        transformation_kind="de_novo_graph_extension",
                        source_candidate_identifiers=(
                            parent.candidate_identifier,
                        ),
                        target_candidate_type=_CANDIDATE_TYPE,
                        generator_identifier=self.generator_identifier,
                        rationale=(
                            "A valence-checked graph extension was applied under "
                            "the declared de novo construction policy."
                        ),
                        parameters={
                            "extension": extension,
                            "parent_atom_index": atom_index,
                            "construction_policy_identifier": self._policy,
                        },
                    )
                    proposals.append(
                        DiscoveryCandidate(
                            candidate_identifier=_identifier(
                                "candidate-molecule",
                                request.generation,
                                smiles,
                            ),
                            candidate_type=_CANDIDATE_TYPE,
                            generation=request.generation,
                            generated_by=self.generator_identifier,
                            representation_artifacts=(pointer,),
                            objective_identifiers=request.objective_identifiers,
                            parent_candidate_identifiers=(
                                parent.candidate_identifier,
                            ),
                            transformation=transformation,
                            metadata={
                                "canonical_smiles": smiles,
                                "generation_kind": "de_novo_graph_extension",
                                "construction_policy_identifier": self._policy,
                            },
                        )
                    )
                    if len(proposals) >= request.max_candidates:
                        break
                if len(proposals) >= request.max_candidates:
                    break
        return CandidateGenerationResult(
            result_identifier=_identifier(
                "generation-result",
                request.request_identifier,
                *(item.candidate_identifier for item in proposals),
            ),
            generator_identifier=self.generator_identifier,
            request_sha256=request.fingerprint,
            candidates=tuple(proposals),
        )


class RDKitMolecularValidityChecker:
    """Sanitization, valence, identity, element, charge, and drug-like filters."""

    def __init__(
        self,
        store: MolecularCandidateArtifactStore,
        *,
        allowed_atomic_numbers: frozenset[int] = frozenset({1, 6, 7, 8, 9, 15, 16, 17, 35, 53}),
        maximum_absolute_charge: int = 3,
        maximum_heavy_atoms: int = 80,
        maximum_molecular_weight: float = 900.0,
    ) -> None:
        self._store = store
        self._allowed = allowed_atomic_numbers
        self._max_charge = maximum_absolute_charge
        self._max_heavy = maximum_heavy_atoms
        self._max_weight = maximum_molecular_weight
        self._seen: dict[str, str] = {}

    @property
    def checker_identifier(self) -> str:
        return _CHECKER

    @property
    def supported_candidate_types(self) -> tuple[str, ...]:
        return (_CANDIDATE_TYPE,)

    def check(self, candidate: DiscoveryCandidate) -> CandidateValidityResult:
        Chem, _, _, Descriptors, _ = _modules()
        smiles = _read_smiles(self._store, candidate)
        molecule = Chem.MolFromSmiles(smiles)
        checks: list[tuple[str, bool, bool, str]] = []
        if molecule is None:
            checks.append(("rdkit_sanitization", False, True, "RDKit sanitization failed."))
            canonical = smiles
        else:
            try:
                Chem.SanitizeMol(molecule)
                sanitized = True
            except Exception:
                sanitized = False
            checks.append(("rdkit_sanitization", sanitized, True, "RDKit sanitization and valence."))
            canonical = str(Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True))
            elements_ok = all(atom.GetAtomicNum() in self._allowed for atom in molecule.GetAtoms())
            charge = sum(atom.GetFormalCharge() for atom in molecule.GetAtoms())
            heavy = int(molecule.GetNumHeavyAtoms())
            weight = float(Descriptors.MolWt(molecule))
            checks.extend((
                ("allowed_elements", elements_ok, True, "Elements must be in the configured medicinal-chemistry set."),
                ("formal_charge_bound", abs(charge) <= self._max_charge, True, "Absolute formal charge must remain bounded."),
                ("heavy_atom_bound", heavy <= self._max_heavy, True, "Heavy-atom count must remain bounded."),
                ("molecular_weight_bound", weight <= self._max_weight, False, "Molecular weight is a configurable soft drug-like filter."),
            ))
        duplicate_of = self._seen.get(canonical)
        duplicate = duplicate_of is not None and duplicate_of != candidate.candidate_identifier
        checks.append(("duplicate_identity", not duplicate, True, "Canonical molecular identity must be unique in the campaign."))
        self._seen.setdefault(canonical, candidate.candidate_identifier)
        findings = tuple(CandidateConstraintResult(
            result_identifier=_identifier("validity-finding", candidate.candidate_identifier, name),
            candidate_identifier=candidate.candidate_identifier,
            candidate_sha256=candidate.fingerprint,
            constraint_identifier=name,
            passed=passed,
            hard=hard,
            rationale=rationale,
        ) for name, passed, hard, rationale in checks)
        return CandidateValidityResult(
            result_identifier=_identifier("validity-result", candidate.candidate_identifier, candidate.fingerprint),
            checker_identifier=self.checker_identifier,
            candidate_identifier=candidate.candidate_identifier,
            candidate_sha256=candidate.fingerprint,
            valid=not any(not item.passed and item.hard for item in findings),
            findings=findings,
        )


class RDKitMolecularDescriptorEvaluator:
    """Real 3D conformer and molecular-descriptor evidence for objectives."""

    def __init__(
        self,
        store: MolecularCandidateArtifactStore,
        *,
        objective_metrics: Mapping[str, str],
        random_seed: int = 20260808,
    ) -> None:
        self._store = store
        self._objective_metrics = dict(objective_metrics)
        self._seed = random_seed

    @property
    def evaluator_identifier(self) -> str:
        return _EVALUATOR

    @property
    def supported_candidate_types(self) -> tuple[str, ...]:
        return (_CANDIDATE_TYPE,)

    @property
    def supported_objective_identifiers(self) -> tuple[str, ...]:
        return tuple(sorted(self._objective_metrics))

    @property
    def stage_identifier(self) -> str:
        return "molecular_3d_descriptors"

    @property
    def stage_order(self) -> int:
        return 10

    @property
    def expensive(self) -> bool:
        return False

    def evaluate(self, candidate: DiscoveryCandidate) -> CandidateEvaluationResult:
        Chem, AllChem, Crippen, Descriptors, extra = _modules()
        Lipinski, QED = extra
        molecule = Chem.AddHs(Chem.MolFromSmiles(_read_smiles(self._store, candidate)))
        parameters = AllChem.ETKDGv3()
        parameters.randomSeed = self._seed
        parameters.numThreads = 1
        embedded = int(AllChem.EmbedMolecule(molecule, parameters)) == 0
        force_field = "none"
        conformer_energy: float | None = None
        if embedded and AllChem.MMFFHasAllMoleculeParams(molecule):
            properties = AllChem.MMFFGetMoleculeProperties(molecule, mmffVariant="MMFF94s")
            field = AllChem.MMFFGetMoleculeForceField(molecule, properties)
            if field is not None:
                field.Minimize(maxIts=500)
                conformer_energy = float(field.CalcEnergy())
                force_field = "mmff94s"
        descriptors = {
            "molecular_weight": float(Descriptors.MolWt(molecule)),
            "logp": float(Crippen.MolLogP(molecule)),
            "qed": float(QED.qed(molecule)),
            "hbond_donors": int(Lipinski.NumHDonors(molecule)),
            "hbond_acceptors": int(Lipinski.NumHAcceptors(molecule)),
            "rotatable_bonds": int(Lipinski.NumRotatableBonds(molecule)),
            "formal_charge": int(sum(atom.GetFormalCharge() for atom in molecule.GetAtoms())),
        }
        evidence_payload = json.dumps({
            "candidate_identifier": candidate.candidate_identifier,
            "canonical_smiles": _read_smiles(self._store, candidate),
            "conformer_generated": embedded,
            "force_field": force_field,
            "conformer_energy_kcal_per_mol": conformer_energy,
            "descriptors": descriptors,
            "score_semantics": "rdkit_descriptor_not_binding_free_energy",
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")
        evidence_pointer = self._store.put("molecular_candidate_evaluation", evidence_payload)
        evidence_identifier = _identifier("candidate-evidence", candidate.candidate_identifier, evidence_pointer.content_sha256)
        evidence = CandidateEvidenceRecord(
            evidence_identifier=evidence_identifier,
            candidate_identifier=candidate.candidate_identifier,
            candidate_sha256=candidate.fingerprint,
            stage_identifier=self.stage_identifier,
            stage_order=self.stage_order,
            evaluator_identifier=self.evaluator_identifier,
            artifacts=(evidence_pointer,),
            metadata={"conformer_generated": embedded, "force_field": force_field},
        )
        scores: list[CandidateObjectiveScore] = []
        for objective_identifier in candidate.objective_identifiers:
            metric = self._objective_metrics.get(objective_identifier)
            if metric not in descriptors:
                continue
            value = float(descriptors[metric])
            if metric == "qed":
                normalized = value
            elif metric == "logp":
                normalized = 1.0 / (1.0 + abs(value - 2.0))
            elif metric == "molecular_weight":
                normalized = 1.0 / (1.0 + abs(value - 350.0) / 100.0)
            else:
                normalized = 1.0 / (1.0 + abs(value))
            scores.append(CandidateObjectiveScore(
                score_identifier=_identifier("objective-score", candidate.candidate_identifier, objective_identifier),
                candidate_identifier=candidate.candidate_identifier,
                candidate_sha256=candidate.fingerprint,
                objective_identifier=objective_identifier,
                value=value,
                normalized_value=max(0.0, min(1.0, normalized)),
                satisfies_objective=True,
                evidence_identifiers=(evidence_identifier,),
            ))
        conformer_constraint = CandidateConstraintResult(
            result_identifier=_identifier("conformer-constraint", candidate.candidate_identifier),
            candidate_identifier=candidate.candidate_identifier,
            candidate_sha256=candidate.fingerprint,
            constraint_identifier="three_dimensional_conformer",
            passed=embedded,
            hard=True,
            rationale="A real RDKit ETKDGv3 conformer is required for downstream pose work.",
            evidence_identifiers=(evidence_identifier,),
        )
        return CandidateEvaluationResult(
            result_identifier=_identifier("evaluation-result", candidate.candidate_identifier, evidence_pointer.content_sha256),
            evaluator_identifier=self.evaluator_identifier,
            candidate_identifier=candidate.candidate_identifier,
            candidate_sha256=candidate.fingerprint,
            stage_identifier=self.stage_identifier,
            stage_order=self.stage_order,
            evidence_records=(evidence,),
            objective_scores=tuple(scores),
            constraint_results=(conformer_constraint,),
            expensive=False,
        )


class VinaMolecularDockingEvaluator:
    """Native Meeko/Vina pose evaluation for a prepared receptor and pocket."""

    def __init__(
        self,
        store: MolecularCandidateArtifactStore,
        *,
        receptor_pdbqt: ArtifactPointer,
        objective_identifier: str,
        box_center_angstrom: tuple[float, float, float],
        box_size_angstrom: tuple[float, float, float],
        exhaustiveness: int = 8,
        pose_count: int = 9,
        random_seed: int = 20260808,
    ) -> None:
        if any(not math.isfinite(value) for value in (*box_center_angstrom, *box_size_angstrom)):
            raise ValueError("Docking box coordinates must be finite.")
        if any(value <= 0 or value > 126 for value in box_size_angstrom):
            raise ValueError("Docking box dimensions are outside Vina bounds.")
        if not 1 <= exhaustiveness <= 128 or not 1 <= pose_count <= 20:
            raise ValueError("Docking search controls are outside their bounds.")
        self._store = store
        self._receptor = receptor_pdbqt
        self._objective = objective_identifier
        self._center = box_center_angstrom
        self._size = box_size_angstrom
        self._exhaustiveness = exhaustiveness
        self._pose_count = pose_count
        self._seed = random_seed

    @property
    def evaluator_identifier(self) -> str:
        return _DOCKING_EVALUATOR

    @property
    def supported_candidate_types(self) -> tuple[str, ...]:
        return (_CANDIDATE_TYPE,)

    @property
    def supported_objective_identifiers(self) -> tuple[str, ...]:
        return (self._objective,)

    @property
    def stage_identifier(self) -> str:
        return "autodock_vina_pose_generation"

    @property
    def stage_order(self) -> int:
        return 20

    @property
    def expensive(self) -> bool:
        return True

    def evaluate(self, candidate: DiscoveryCandidate) -> CandidateEvaluationResult:
        from meeko import MoleculePreparation, PDBQTWriterLegacy
        from rdkit import Chem
        from rdkit.Chem import AllChem
        from vina import Vina

        receptor_bytes = self._store.read(self._receptor)
        if hashlib.sha256(receptor_bytes).hexdigest() != self._receptor.content_sha256:
            raise ValueError("Docking receptor bytes fail content addressing.")
        if b"ATOM" not in receptor_bytes and b"HETATM" not in receptor_bytes:
            raise ValueError("Docking receptor PDBQT contains no atoms.")
        molecule = Chem.AddHs(Chem.MolFromSmiles(_read_smiles(self._store, candidate)))
        embedding = AllChem.ETKDGv3()
        embedding.randomSeed = self._seed
        embedding.numThreads = 1
        if int(AllChem.EmbedMolecule(molecule, embedding)) != 0:
            raise ValueError("Candidate 3D conformer generation failed.")
        if not AllChem.MMFFHasAllMoleculeParams(molecule):
            raise ValueError("Candidate lacks complete MMFF94s preparation parameters.")
        properties = AllChem.MMFFGetMoleculeProperties(molecule, mmffVariant="MMFF94s")
        field = AllChem.MMFFGetMoleculeForceField(molecule, properties)
        if field is None:
            raise ValueError("Candidate MMFF94s force-field construction failed.")
        field.Minimize(maxIts=500)
        preparation = MoleculePreparation(charge_model="gasteiger", add_index_map=True)
        setups = preparation.prepare(molecule, conformer_id=0)
        if len(setups) != 1:
            raise ValueError("Candidate Meeko preparation did not yield one ligand.")
        ligand_pdbqt, success, error = PDBQTWriterLegacy.write_string(
            setups[0], add_index_map=True, remove_smiles=False
        )
        if not success or error or "ROOT" not in ligand_pdbqt:
            raise ValueError("Candidate Meeko ligand PDBQT is incomplete.")
        with tempfile.TemporaryDirectory(prefix="cgr-discovery-vina-") as directory:
            root = Path(directory)
            receptor_path = root / "receptor.pdbqt"
            ligand_path = root / "ligand.pdbqt"
            receptor_path.write_bytes(receptor_bytes)
            ligand_path.write_text(ligand_pdbqt, encoding="utf-8")
            engine = Vina(sf_name="vina", cpu=1, seed=self._seed, no_refine=False, verbosity=0)
            engine.set_receptor(str(receptor_path))
            engine.set_ligand_from_file(str(ligand_path))
            engine.compute_vina_maps(center=list(self._center), box_size=list(self._size))
            engine.dock(exhaustiveness=self._exhaustiveness, n_poses=self._pose_count)
            energies = engine.energies(n_poses=self._pose_count)
            poses_pdbqt = engine.poses(n_poses=self._pose_count, energy_range=5.0)
        if len(energies) < 1 or not poses_pdbqt.strip():
            raise ValueError("Vina produced no candidate docking pose evidence.")
        best_score = float(energies[0][0])
        if not math.isfinite(best_score):
            raise ValueError("Vina produced a non-finite docking score.")
        pose_pointer = self._store.put(
            "molecular_candidate_docking_poses_pdbqt", poses_pdbqt.encode("utf-8")
        )
        evidence_payload = json.dumps({
            "candidate_identifier": candidate.candidate_identifier,
            "receptor_sha256": self._receptor.content_sha256,
            "box_center_angstrom": self._center,
            "box_size_angstrom": self._size,
            "exhaustiveness": self._exhaustiveness,
            "requested_pose_count": self._pose_count,
            "returned_pose_count": len(energies),
            "vina_scores_kcal_per_mol": [float(row[0]) for row in energies],
            "score_semantics": "autodock_vina_score_not_binding_free_energy",
            "atom_mapping": "meeko_index_map_embedded_in_pdbqt",
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")
        evidence_pointer = self._store.put("molecular_candidate_docking_evaluation", evidence_payload)
        evidence_identifier = _identifier(
            "candidate-docking-evidence", candidate.candidate_identifier, evidence_pointer.content_sha256
        )
        evidence = CandidateEvidenceRecord(
            evidence_identifier=evidence_identifier,
            candidate_identifier=candidate.candidate_identifier,
            candidate_sha256=candidate.fingerprint,
            stage_identifier=self.stage_identifier,
            stage_order=self.stage_order,
            evaluator_identifier=self.evaluator_identifier,
            artifacts=(evidence_pointer, pose_pointer),
            metadata={
                "vina_score_kcal_per_mol": best_score,
                "binding_free_energy_calculated": False,
                "pose_count": len(energies),
            },
        )
        normalized = 1.0 / (1.0 + math.exp(max(-50.0, min(50.0, best_score + 7.0))))
        score = CandidateObjectiveScore(
            score_identifier=_identifier(
                "objective-docking-score", candidate.candidate_identifier, self._objective
            ),
            candidate_identifier=candidate.candidate_identifier,
            candidate_sha256=candidate.fingerprint,
            objective_identifier=self._objective,
            value=best_score,
            normalized_value=normalized,
            satisfies_objective=True,
            evidence_identifiers=(evidence_identifier,),
        )
        constraint = CandidateConstraintResult(
            result_identifier=_identifier("docking-pose-constraint", candidate.candidate_identifier),
            candidate_identifier=candidate.candidate_identifier,
            candidate_sha256=candidate.fingerprint,
            constraint_identifier="native_vina_pose_generated",
            passed=True,
            hard=True,
            rationale="A native Vina pose and finite score were persisted.",
            evidence_identifiers=(evidence_identifier,),
        )
        return CandidateEvaluationResult(
            result_identifier=_identifier(
                "docking-evaluation-result", candidate.candidate_identifier, evidence_pointer.content_sha256
            ),
            evaluator_identifier=self.evaluator_identifier,
            candidate_identifier=candidate.candidate_identifier,
            candidate_sha256=candidate.fingerprint,
            stage_identifier=self.stage_identifier,
            stage_order=self.stage_order,
            evidence_records=(evidence,),
            objective_scores=(score,),
            constraint_results=(constraint,),
            expensive=True,
        )


class MolecularCandidateEvidenceVerifier:
    """Reject objective scores lacking real evaluator artifacts or physical validity."""

    @property
    def verifier_identifier(self) -> str:
        return _VERIFIER

    @property
    def supported_candidate_types(self) -> tuple[str, ...]:
        return (_CANDIDATE_TYPE,)

    @property
    def objective_families(self) -> tuple[str, ...]:
        return ("candidate_ranking",)

    def verify(
        self,
        candidate: DiscoveryCandidate,
        assessment: CandidateAssessment,
        objective_family: str,
    ) -> ScientificVerificationReport:
        evidence_ids = {item.evidence_identifier for item in assessment.evidence_records}
        complete_scores = bool(assessment.objective_scores) and all(
            score.evidence_identifiers
            and set(score.evidence_identifiers).issubset(evidence_ids)
            and math.isfinite(score.value)
            for score in assessment.objective_scores
        )
        constraints_passed = all(
            result.passed or not result.hard for result in assessment.constraint_results
        )
        identity_valid = (
            assessment.candidate_identifier == candidate.candidate_identifier
            and assessment.candidate_sha256 == candidate.fingerprint
        )
        pass_by_dimension = {
            VerificationDimension.EXECUTION_INTEGRITY: bool(evidence_ids),
            VerificationDimension.IDENTITY_INTEGRITY: identity_valid,
            VerificationDimension.NUMERICAL_INTEGRITY: complete_scores,
            VerificationDimension.SCIENTIFIC_PLAUSIBILITY: constraints_passed,
            VerificationDimension.CROSS_CALCULATION_COMPARABILITY: complete_scores,
            VerificationDimension.UNCERTAINTY: complete_scores,
            VerificationDimension.AUTHORIZATION: True,
        }
        dimensions: list[DimensionVerificationResult] = []
        for dimension in VERIFICATION_DIMENSION_ORDER:
            passed = pass_by_dimension[dimension]
            finding = () if passed else (ScientificVerificationFinding(
                finding_identifier=_identifier("molecular-verification-finding", candidate.candidate_identifier, dimension.value),
                dimension=dimension,
                severity=ScientificVerificationSeverity.ERROR,
                message="Molecular candidate evidence is incomplete or invalid for this dimension.",
                blocking=True,
                subject_identifier=candidate.candidate_identifier,
                evidence_identifiers=tuple(sorted(evidence_ids)),
            ),)
            dimensions.append(DimensionVerificationResult(
                dimension=dimension,
                outcome=VerificationOutcome.PASSED if passed else VerificationOutcome.FAILED,
                findings=finding,
                summary=(
                    "Molecular candidate evidence passed this verification dimension."
                    if passed else
                    "Molecular candidate evidence failed this verification dimension."
                ),
                evidence_identifiers=tuple(sorted(evidence_ids)),
            ))
        overall_passed = all(pass_by_dimension.values())
        return ScientificVerificationReport(
            report_identifier=_identifier("molecular-candidate-verification", candidate.candidate_identifier, assessment.fingerprint),
            verifier_identifier=self.verifier_identifier,
            verifier_version=_VERSION,
            objective_family=objective_family,
            subject_identifier=candidate.candidate_identifier,
            request_sha256=assessment.fingerprint,
            dimension_results=tuple(dimensions),
            overall_outcome=VerificationOutcome.PASSED if overall_passed else VerificationOutcome.FAILED,
            execution_integrity_passed=pass_by_dimension[VerificationDimension.EXECUTION_INTEGRITY],
            scientific_quality_passed=all(
                pass_by_dimension[dimension]
                for dimension in VERIFICATION_DIMENSION_ORDER
                if dimension not in {
                    VerificationDimension.EXECUTION_INTEGRITY,
                    VerificationDimension.AUTHORIZATION,
                }
            ),
            authorization_passed=True,
        )


def molecular_discovery_registries(
    store: MolecularCandidateArtifactStore,
    *,
    seed_smiles: tuple[str, ...],
    objective_metrics: Mapping[str, str],
) -> tuple[object, object, object, object]:
    """Build the four real Phase 7 registries for small-molecule campaigns."""

    from .registry import (
        CandidateEvaluatorRegistry,
        CandidateGeneratorRegistry,
        CandidateValidityCheckerRegistry,
    )
    from .runtime import CandidateScientificVerifierRegistry

    generator = RDKitMolecularCandidateGenerator(
        store,
        seed_smiles=seed_smiles,
    )
    checker = RDKitMolecularValidityChecker(store)
    evaluator = RDKitMolecularDescriptorEvaluator(
        store,
        objective_metrics=objective_metrics,
    )
    verifier = MolecularCandidateEvidenceVerifier()
    return (
        CandidateGeneratorRegistry((generator,)),
        CandidateValidityCheckerRegistry((checker,)),
        CandidateEvaluatorRegistry((evaluator,)),
        CandidateScientificVerifierRegistry((verifier,)),
    )
