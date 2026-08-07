"""Extensible candidate-type-neutral discovery component registries."""

from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable

from cgr.science.canonical import validate_identifier

from .contracts import (
    CandidateEvaluationResult,
    CandidateGenerationRequest,
    CandidateGenerationResult,
    CandidateValidityResult,
    DiscoveryCandidate,
)


class DiscoveryRegistryError(ValueError):
    """Base controlled error for discovery registry operations."""


class DuplicateDiscoveryComponentError(DiscoveryRegistryError):
    """Raised when a registry receives a duplicate component identifier."""


class DiscoveryComponentNotFoundError(DiscoveryRegistryError):
    """Raised when a requested discovery component is not registered."""


def _supported_types(values: tuple[str, ...]) -> tuple[str, ...]:
    validated = tuple(
        validate_identifier(item, label="supported candidate type") for item in values
    )
    if len(validated) != len(set(validated)):
        raise ValueError("Supported candidate types must be unique.")
    return tuple(sorted(validated))


def _supports(values: tuple[str, ...], candidate_type: str) -> bool:
    normalized = validate_identifier(candidate_type, label="candidate type")
    supported = _supported_types(values)
    return not supported or normalized in supported


@runtime_checkable
class CandidateGenerator(Protocol):
    """Protocol for a proposal-only discovery generator."""

    @property
    def generator_identifier(self) -> str:
        """Stable generator identifier."""
        ...

    @property
    def supported_candidate_types(self) -> tuple[str, ...]:
        """Candidate types produced by this generator; empty means extensible/universal."""
        ...

    def propose(self, request: CandidateGenerationRequest) -> CandidateGenerationResult:
        """Return candidate proposals without granting execution authority."""
        ...


@runtime_checkable
class CandidateValidityChecker(Protocol):
    """Protocol for candidate-type-specific or universal validity checks."""

    @property
    def checker_identifier(self) -> str:
        """Stable checker identifier."""
        ...

    @property
    def supported_candidate_types(self) -> tuple[str, ...]:
        """Candidate types accepted by this checker; empty means universal."""
        ...

    def check(self, candidate: DiscoveryCandidate) -> CandidateValidityResult:
        """Return validity evidence without mutating the candidate."""
        ...


@runtime_checkable
class CandidateEvaluator(Protocol):
    """Protocol for one staged scientific candidate evaluator."""

    @property
    def evaluator_identifier(self) -> str:
        """Stable evaluator identifier."""
        ...

    @property
    def supported_candidate_types(self) -> tuple[str, ...]:
        """Candidate types accepted by this evaluator; empty means universal."""
        ...

    @property
    def stage_identifier(self) -> str:
        """Extensible evaluation-stage identifier."""
        ...

    @property
    def stage_order(self) -> int:
        """Deterministic ascending evaluation-stage order."""
        ...

    def evaluate(self, candidate: DiscoveryCandidate) -> CandidateEvaluationResult:
        """Return immutable scientific evidence for a candidate."""
        ...


class CandidateGeneratorRegistry:
    """Registry of proposal generators routed only by extensible candidate type."""

    def __init__(self, generators: Iterable[CandidateGenerator] = ()) -> None:
        self._generators: dict[str, CandidateGenerator] = {}
        for generator in generators:
            self.register(generator)

    def register(self, generator: CandidateGenerator) -> None:
        if not isinstance(generator, CandidateGenerator):
            raise TypeError("Discovery generators must implement CandidateGenerator.")
        identifier = validate_identifier(
            generator.generator_identifier,
            label="candidate generator identifier",
        )
        _supported_types(generator.supported_candidate_types)
        if identifier in self._generators:
            raise DuplicateDiscoveryComponentError(
                f"Candidate generator '{identifier}' is already registered."
            )
        self._generators[identifier] = generator

    def get(self, identifier: str) -> CandidateGenerator:
        normalized = validate_identifier(
            identifier, label="candidate generator identifier"
        )
        try:
            return self._generators[normalized]
        except KeyError as exc:
            raise DiscoveryComponentNotFoundError(
                f"Candidate generator '{identifier}' is not registered."
            ) from exc

    def resolve(self, candidate_type: str) -> tuple[CandidateGenerator, ...]:
        return tuple(
            self._generators[identifier]
            for identifier in sorted(self._generators)
            if _supports(
                self._generators[identifier].supported_candidate_types,
                candidate_type,
            )
        )

    def identifiers(self) -> tuple[str, ...]:
        return tuple(sorted(self._generators))


class CandidateValidityCheckerRegistry:
    """Registry of deterministic validity checks routed by candidate type."""

    def __init__(self, checkers: Iterable[CandidateValidityChecker] = ()) -> None:
        self._checkers: dict[str, CandidateValidityChecker] = {}
        for checker in checkers:
            self.register(checker)

    def register(self, checker: CandidateValidityChecker) -> None:
        if not isinstance(checker, CandidateValidityChecker):
            raise TypeError(
                "Validity checkers must implement CandidateValidityChecker."
            )
        identifier = validate_identifier(
            checker.checker_identifier,
            label="candidate validity checker identifier",
        )
        _supported_types(checker.supported_candidate_types)
        if identifier in self._checkers:
            raise DuplicateDiscoveryComponentError(
                f"Candidate validity checker '{identifier}' is already registered."
            )
        self._checkers[identifier] = checker

    def get(self, identifier: str) -> CandidateValidityChecker:
        normalized = validate_identifier(
            identifier,
            label="candidate validity checker identifier",
        )
        try:
            return self._checkers[normalized]
        except KeyError as exc:
            raise DiscoveryComponentNotFoundError(
                f"Candidate validity checker '{identifier}' is not registered."
            ) from exc

    def resolve(self, candidate_type: str) -> tuple[CandidateValidityChecker, ...]:
        return tuple(
            self._checkers[identifier]
            for identifier in sorted(self._checkers)
            if _supports(
                self._checkers[identifier].supported_candidate_types,
                candidate_type,
            )
        )

    def identifiers(self) -> tuple[str, ...]:
        return tuple(sorted(self._checkers))


class CandidateEvaluatorRegistry:
    """Registry of staged scientific evaluators routed by candidate type."""

    def __init__(self, evaluators: Iterable[CandidateEvaluator] = ()) -> None:
        self._evaluators: dict[str, CandidateEvaluator] = {}
        for evaluator in evaluators:
            self.register(evaluator)

    def register(self, evaluator: CandidateEvaluator) -> None:
        if not isinstance(evaluator, CandidateEvaluator):
            raise TypeError("Candidate evaluators must implement CandidateEvaluator.")
        identifier = validate_identifier(
            evaluator.evaluator_identifier,
            label="candidate evaluator identifier",
        )
        validate_identifier(
            evaluator.stage_identifier, label="evaluation stage identifier"
        )
        if isinstance(evaluator.stage_order, bool) or not isinstance(
            evaluator.stage_order, int
        ):
            raise ValueError("Candidate evaluator stage order must be an integer.")
        if evaluator.stage_order < 0:
            raise ValueError("Candidate evaluator stage order cannot be negative.")
        _supported_types(evaluator.supported_candidate_types)
        if identifier in self._evaluators:
            raise DuplicateDiscoveryComponentError(
                f"Candidate evaluator '{identifier}' is already registered."
            )
        self._evaluators[identifier] = evaluator

    def get(self, identifier: str) -> CandidateEvaluator:
        normalized = validate_identifier(
            identifier, label="candidate evaluator identifier"
        )
        try:
            return self._evaluators[normalized]
        except KeyError as exc:
            raise DiscoveryComponentNotFoundError(
                f"Candidate evaluator '{identifier}' is not registered."
            ) from exc

    def resolve(self, candidate_type: str) -> tuple[CandidateEvaluator, ...]:
        matched = [
            evaluator
            for evaluator in self._evaluators.values()
            if _supports(evaluator.supported_candidate_types, candidate_type)
        ]
        return tuple(
            sorted(
                matched,
                key=lambda evaluator: (
                    evaluator.stage_order,
                    evaluator.stage_identifier,
                    evaluator.evaluator_identifier,
                ),
            )
        )

    def identifiers(self) -> tuple[str, ...]:
        return tuple(sorted(self._evaluators))
