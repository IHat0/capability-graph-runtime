"""Concrete RDKit molecular components for the Phase 7 discovery runtime."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from cgr.kernel.contracts import CapabilityVersion
from cgr.science import ArtifactPointer
from cgr.scientific_verification import (
    DimensionVerificationResult,
    ScientificVerificationFinding,
    ScientificVerificationReport,
    ScientificVerificationSeverity,
    VERIFICATION_DIMENSION_ORDER,
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
_CHECKER = "checker.rdkit_druglike_validity"
_EVALUATOR = "evaluator.rdkit_3d_descriptors"
_VERIFIER = "verifier.molecular_candidate_evidence"


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
    from rdkit.Chem import AllChem, Crippen, Descriptors, Lipinski, QED

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
        Chem, _, _, _, _ = _modules()
        molecule = Chem.MolFromSmiles(smiles)
        if molecule is None:
            raise ValueError("Seed or generated SMILES is invalid.")
        Chem.SanitizeMol(molecule)
        return str(Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True))

    def _mutations(self, smiles: str) -> tuple[tuple[str, str, int], ...]:
        Chem, _, _, _, _ = _modules()
        molecule = Chem.MolFromSmiles(smiles)
        if molecule is None:
            raise ValueError("Parent SMILES is invalid.")
        generated: dict[str, tuple[str, int]] = {}
        for atom in molecule.GetAtoms():
            if atom.GetTotalNumHs() <= 0 or atom.GetIsAromatic() and atom.GetSymbol() == "N":
                continue
            for symbol in self._substituents:
                editable = Chem.RWMol(molecule)
                new_index = editable.AddAtom(Chem.Atom(symbol))
                editable.AddBond(atom.GetIdx(), new_index, Chem.BondType.SINGLE)
                candidate = editable.GetMol()
                try:
                    Chem.SanitizeMol(candidate)
                except Exception:
                    continue
                canonical = str(Chem.MolToSmiles(
                    candidate, canonical=True, isomericSmiles=True
                ))
                generated.setdefault(canonical, (symbol, int(atom.GetIdx())))
        return tuple(
            (canonical, symbol, atom_index)
            for canonical, (symbol, atom_index) in sorted(generated.items())
        )

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
