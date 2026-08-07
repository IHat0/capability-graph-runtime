"""Common seven-dimension verification framework and extensible verifier registry."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from cgr.kernel.contracts import CapabilityVersion
from cgr.science import BoundedMetadata
from cgr.science.canonical import validate_identifier

from .contracts import (
    VERIFICATION_DIMENSION_ORDER,
    CalculationEvidence,
    DimensionVerificationResult,
    ScientificVerificationFinding,
    ScientificVerificationReport,
    ScientificVerificationRequest,
    ScientificVerificationRequestType,
    ScientificVerificationSeverity,
    VerificationDimension,
    VerificationOutcome,
)

VERIFIER_VERSION = CapabilityVersion(major=1, minor=0, patch=0)


class ScientificVerifierRegistryError(ValueError):
    """Base controlled error for scientific-verifier registry operations."""


class DuplicateScientificVerifierError(ScientificVerifierRegistryError):
    """Raised when two verifiers claim one objective family."""


class ScientificVerifierNotFoundError(ScientificVerifierRegistryError):
    """Raised when no verifier is registered for an objective family."""


@runtime_checkable
class ObjectiveScientificVerifier(Protocol):
    """Extensible objective-specific verifier interface."""

    @property
    def objective_family(self) -> str:
        """Return the objective family handled by this verifier."""
        ...

    @property
    def verifier_identifier(self) -> str:
        """Return the stable verifier identifier."""
        ...

    def verify(
        self, request: ScientificVerificationRequestType
    ) -> ScientificVerificationReport:
        """Verify one engine-neutral scientific objective request."""
        ...


def finding(
    identifier: str,
    dimension: VerificationDimension,
    message: str,
    *,
    subject_identifier: str | None = None,
    expected: str | int | float | bool | None = None,
    observed: str | int | float | bool | None = None,
    evidence_identifiers: tuple[str, ...] = (),
    blocking: bool = True,
    severity: ScientificVerificationSeverity | None = None,
) -> ScientificVerificationFinding:
    """Construct one deterministic finding with fail-closed severity defaults."""

    effective_severity = severity or (
        ScientificVerificationSeverity.ERROR
        if blocking
        else ScientificVerificationSeverity.WARNING
    )
    return ScientificVerificationFinding(
        finding_identifier=identifier,
        dimension=dimension,
        severity=effective_severity,
        message=message,
        blocking=blocking,
        subject_identifier=subject_identifier,
        expected=expected,
        observed=observed,
        evidence_identifiers=evidence_identifiers,
    )


class BaseObjectiveScientificVerifier:
    """Shared integrity checks and report assembly for objective-specific verifiers."""

    objective_family: str
    verifier_identifier: str

    def verify(
        self, request: ScientificVerificationRequestType
    ) -> ScientificVerificationReport:
        if not isinstance(request, ScientificVerificationRequest):
            raise TypeError(
                "Scientific verifiers require a scientific verification request."
            )
        if request.objective_family != self.objective_family:
            raise ValueError(
                "Scientific verifier objective family does not match the request."
            )

        findings = [
            *self._common_findings(request),
            *self.objective_findings(request),
        ]
        metrics = self.dimension_metrics(request)
        evidence_identifiers = tuple(
            item.calculation_identifier for item in request.calculations
        )
        by_dimension: dict[
            VerificationDimension, list[ScientificVerificationFinding]
        ] = {dimension: [] for dimension in VERIFICATION_DIMENSION_ORDER}
        for item in findings:
            by_dimension[item.dimension].append(item)

        dimension_results: list[DimensionVerificationResult] = []
        for dimension in VERIFICATION_DIMENSION_ORDER:
            dimension_findings = tuple(by_dimension[dimension])
            failed = any(item.blocking for item in dimension_findings)
            outcome = (
                VerificationOutcome.FAILED if failed else VerificationOutcome.PASSED
            )
            summary = (
                f"{dimension.value} failed with "
                f"{sum(item.blocking for item in dimension_findings)} blocking finding(s)."
                if failed
                else f"{dimension.value} passed."
            )
            dimension_results.append(
                DimensionVerificationResult(
                    dimension=dimension,
                    outcome=outcome,
                    findings=dimension_findings,
                    summary=summary,
                    evidence_identifiers=evidence_identifiers,
                    metrics=metrics.get(dimension, {}),
                )
            )

        canonical_results = tuple(dimension_results)
        result_by_dimension = {item.dimension: item for item in canonical_results}
        execution_passed = (
            result_by_dimension[VerificationDimension.EXECUTION_INTEGRITY].outcome
            is VerificationOutcome.PASSED
        )
        authorization_passed = (
            result_by_dimension[VerificationDimension.AUTHORIZATION].outcome
            is VerificationOutcome.PASSED
        )
        scientific_dimensions = (
            VerificationDimension.IDENTITY_INTEGRITY,
            VerificationDimension.NUMERICAL_INTEGRITY,
            VerificationDimension.SCIENTIFIC_PLAUSIBILITY,
            VerificationDimension.CROSS_CALCULATION_COMPARABILITY,
            VerificationDimension.UNCERTAINTY,
        )
        scientific_quality_passed = all(
            result_by_dimension[dimension].outcome is VerificationOutcome.PASSED
            for dimension in scientific_dimensions
        )
        overall = (
            VerificationOutcome.PASSED
            if all(
                item.outcome is VerificationOutcome.PASSED for item in canonical_results
            )
            else VerificationOutcome.FAILED
        )
        report_identifier = self._report_identifier(request)
        return ScientificVerificationReport(
            report_identifier=report_identifier,
            verifier_identifier=self.verifier_identifier,
            verifier_version=VERIFIER_VERSION,
            objective_family=self.objective_family,
            subject_identifier=request.subject_identifier,
            request_sha256=request.fingerprint,
            dimension_results=canonical_results,
            overall_outcome=overall,
            execution_integrity_passed=execution_passed,
            scientific_quality_passed=scientific_quality_passed,
            authorization_passed=authorization_passed,
        )

    def objective_findings(
        self, request: ScientificVerificationRequestType
    ) -> tuple[ScientificVerificationFinding, ...]:
        """Return objective-specific findings."""
        raise NotImplementedError

    def dimension_metrics(
        self, request: ScientificVerificationRequestType
    ) -> dict[VerificationDimension, BoundedMetadata]:
        """Return bounded objective-specific metrics for report dimensions."""
        return {}

    def _common_findings(
        self, request: ScientificVerificationRequest
    ) -> tuple[ScientificVerificationFinding, ...]:
        findings: list[ScientificVerificationFinding] = []
        calculations = request.calculations

        for calculation in calculations:
            evidence = (calculation.calculation_identifier,)
            if not calculation.execution_completed:
                findings.append(
                    finding(
                        "verification.execution.incomplete",
                        VerificationDimension.EXECUTION_INTEGRITY,
                        "Calculation did not produce completed execution evidence.",
                        subject_identifier=calculation.calculation_identifier,
                        expected=True,
                        observed=False,
                        evidence_identifiers=evidence,
                    )
                )
            if not calculation.execution_successful:
                findings.append(
                    finding(
                        "verification.execution.unsuccessful",
                        VerificationDimension.EXECUTION_INTEGRITY,
                        "Calculation execution did not report success.",
                        subject_identifier=calculation.calculation_identifier,
                        expected=True,
                        observed=False,
                        evidence_identifiers=evidence,
                    )
                )
            if not calculation.identity_valid:
                findings.append(
                    finding(
                        "verification.identity.invalid",
                        VerificationDimension.IDENTITY_INTEGRITY,
                        "Calculation artifact identity or lineage was not validated.",
                        subject_identifier=calculation.calculation_identifier,
                        expected=True,
                        observed=False,
                        evidence_identifiers=evidence,
                    )
                )
            if not calculation.numerically_converged:
                findings.append(
                    finding(
                        "verification.numerical.not_converged",
                        VerificationDimension.NUMERICAL_INTEGRITY,
                        "Calculation did not satisfy its declared numerical convergence condition.",
                        subject_identifier=calculation.calculation_identifier,
                        expected=True,
                        observed=False,
                        evidence_identifiers=evidence,
                    )
                )
            if not calculation.values_finite:
                findings.append(
                    finding(
                        "verification.numerical.nonfinite",
                        VerificationDimension.NUMERICAL_INTEGRITY,
                        "Calculation contains non-finite numerical evidence.",
                        subject_identifier=calculation.calculation_identifier,
                        expected=True,
                        observed=False,
                        evidence_identifiers=evidence,
                    )
                )
            if request.authorization_required and not calculation.authorized:
                findings.append(
                    finding(
                        "verification.authorization.missing",
                        VerificationDimension.AUTHORIZATION,
                        "Calculation lacks the authorization required by this objective.",
                        subject_identifier=calculation.calculation_identifier,
                        expected=True,
                        observed=False,
                        evidence_identifiers=evidence,
                    )
                )

        if request.require_same_molecule:
            molecules = {item.molecule_identifier for item in calculations}
            if len(molecules) != 1:
                findings.append(
                    finding(
                        "verification.comparability.molecule_mismatch",
                        VerificationDimension.CROSS_CALCULATION_COMPARABILITY,
                        "Calculations do not share the required molecular identity.",
                        expected="one_molecule_identifier",
                        observed=str(len(molecules)),
                        evidence_identifiers=tuple(
                            item.calculation_identifier for item in calculations
                        ),
                    )
                )

        if request.require_same_method:
            methods = {item.method_sha256 for item in calculations}
            if len(methods) != 1:
                findings.append(
                    finding(
                        "verification.comparability.method_mismatch",
                        VerificationDimension.CROSS_CALCULATION_COMPARABILITY,
                        "Calculations do not share the required method identity.",
                        expected="one_method_sha256",
                        observed=str(len(methods)),
                        evidence_identifiers=tuple(
                            item.calculation_identifier for item in calculations
                        ),
                    )
                )

        if request.require_same_geometry:
            geometries = {item.geometry_sha256 for item in calculations}
            if len(geometries) != 1:
                findings.append(
                    finding(
                        "verification.comparability.geometry_mismatch",
                        VerificationDimension.CROSS_CALCULATION_COMPARABILITY,
                        "Calculations do not share the required geometry identity.",
                        expected="one_geometry_sha256",
                        observed=str(len(geometries)),
                        evidence_identifiers=tuple(
                            item.calculation_identifier for item in calculations
                        ),
                    )
                )

        for calculation in calculations:
            evidence = (calculation.calculation_identifier,)
            if request.require_uncertainty and calculation.uncertainty_hartree is None:
                findings.append(
                    finding(
                        "verification.uncertainty.missing",
                        VerificationDimension.UNCERTAINTY,
                        "Required uncertainty evidence is missing.",
                        subject_identifier=calculation.calculation_identifier,
                        expected="finite_nonnegative_uncertainty",
                        observed="missing",
                        evidence_identifiers=evidence,
                    )
                )
            if (
                request.maximum_uncertainty_hartree is not None
                and calculation.uncertainty_hartree is not None
                and calculation.uncertainty_hartree
                > request.maximum_uncertainty_hartree
            ):
                findings.append(
                    finding(
                        "verification.uncertainty.exceeds_limit",
                        VerificationDimension.UNCERTAINTY,
                        "Calculation uncertainty exceeds the objective limit.",
                        subject_identifier=calculation.calculation_identifier,
                        expected=request.maximum_uncertainty_hartree,
                        observed=calculation.uncertainty_hartree,
                        evidence_identifiers=evidence,
                    )
                )

        return tuple(findings)

    def _report_identifier(self, request: ScientificVerificationRequest) -> str:
        encoded = (f"{self.verifier_identifier}\x1f{request.fingerprint}").encode(
            "utf-8"
        )
        return "scientific-verification-" + hashlib.sha256(encoded).hexdigest()[:32]


class ScientificVerifierRegistry:
    """Deterministic objective-family registry with explicit extension support."""

    def __init__(self, verifiers: Iterable[ObjectiveScientificVerifier] = ()) -> None:
        self._verifiers: dict[str, ObjectiveScientificVerifier] = {}
        for verifier in verifiers:
            self.register(verifier)

    def register(self, verifier: ObjectiveScientificVerifier) -> None:
        if not isinstance(verifier, ObjectiveScientificVerifier):
            raise TypeError(
                "Registered scientific verifiers must implement the verifier protocol."
            )
        family = validate_identifier(
            verifier.objective_family, label="scientific verifier objective family"
        )
        if family in self._verifiers:
            raise DuplicateScientificVerifierError(
                f"A scientific verifier is already registered for {family}."
            )
        self._verifiers[family] = verifier

    def resolve(self, family: str) -> ObjectiveScientificVerifier:
        normalized = validate_identifier(
            family, label="scientific verifier objective family"
        )
        try:
            return self._verifiers[normalized]
        except KeyError as exc:
            raise ScientificVerifierNotFoundError(
                f"No scientific verifier is registered for {family}."
            ) from exc

    def verify(
        self, request: ScientificVerificationRequestType
    ) -> ScientificVerificationReport:
        return self.resolve(request.objective_family).verify(request)

    def families(self) -> tuple[str, ...]:
        return tuple(sorted(self._verifiers))

    def verifiers(self) -> tuple[ObjectiveScientificVerifier, ...]:
        return tuple(self._verifiers[family] for family in self.families())


def calculation_map(
    calculations: tuple[CalculationEvidence, ...],
) -> dict[str, CalculationEvidence]:
    """Return calculation evidence keyed by stable calculation identifier."""

    return {item.calculation_identifier: item for item in calculations}
