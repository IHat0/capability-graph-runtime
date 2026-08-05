"""Scientific-engine adapter contract regressions."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cgr.kernel.contracts import (
    CapabilityVersion,
    ExecutionStatus,
    HealthStatus,
)
from cgr.science.capabilities import CapabilityExecutionEnvelope
from cgr.science.contracts import (
    CapabilityDescriptor,
    CapabilityInvocation,
    CapabilityResult,
    DeterminismClassification,
)
from cgr.science.engine_adapters import (
    ScientificEngineAdapter,
    ScientificEngineAdapterDeclaration,
    ScientificEngineHealthReport,
    ScientificEngineIdentity,
)


VERSION = CapabilityVersion(major=1, minor=0, patch=0)


def _envelope(
    name: str,
    version: CapabilityVersion = VERSION,
) -> CapabilityExecutionEnvelope:
    return CapabilityExecutionEnvelope(
        descriptor=CapabilityDescriptor(
            capability_name=name,
            version=version,
            accepted_artifact_types=("molecular_structure",),
            produced_artifact_types=("scientific_result",),
            determinism=DeterminismClassification.DETERMINISTIC,
        ),
        execution_targets=("target.local",),
    )


def test_engine_identity_preserves_external_version_text() -> None:
    identity = ScientificEngineIdentity(
        engine_identifier="engine.rdkit",
        engine_version=" 2026.03.1+cpu ",
    )

    assert identity.engine_identifier == "engine.rdkit"
    assert identity.engine_version == "2026.03.1+cpu"


def test_engine_identity_rejects_blank_versions() -> None:
    with pytest.raises(ValidationError, match="cannot be blank"):
        ScientificEngineIdentity(
            engine_identifier="engine.rdkit",
            engine_version=" ",
        )


def test_adapter_declaration_orders_exact_capability_identities() -> None:
    declaration = ScientificEngineAdapterDeclaration(
        adapter_identifier="adapter.rdkit",
        adapter_version=VERSION,
        engine=ScientificEngineIdentity(
            engine_identifier="engine.rdkit",
            engine_version="2026.03.1",
        ),
        capabilities=(
            _envelope("structure.sanitize"),
            _envelope("structure.parse_smiles"),
        ),
        metadata={"distribution": "rdkit"},
    )

    assert tuple(identity[0] for identity in declaration.capability_identities) == (
        "structure.parse_smiles",
        "structure.sanitize",
    )


def test_adapter_declaration_requires_at_least_one_capability() -> None:
    with pytest.raises(ValidationError, match="at least one capability"):
        ScientificEngineAdapterDeclaration(
            adapter_identifier="adapter.empty",
            adapter_version=VERSION,
            engine=ScientificEngineIdentity(
                engine_identifier="engine.example",
                engine_version="1.0",
            ),
            capabilities=(),
        )


def test_adapter_declaration_rejects_duplicate_exact_capabilities() -> None:
    envelope = _envelope("structure.parse_smiles")

    with pytest.raises(ValidationError, match="must be unique"):
        ScientificEngineAdapterDeclaration(
            adapter_identifier="adapter.rdkit",
            adapter_version=VERSION,
            engine=ScientificEngineIdentity(
                engine_identifier="engine.rdkit",
                engine_version="2026.03.1",
            ),
            capabilities=(envelope, envelope),
        )


def test_health_report_reuses_kernel_health_status() -> None:
    healthy = ScientificEngineHealthReport(status=HealthStatus.HEALTHY)
    unavailable = ScientificEngineHealthReport(
        status=HealthStatus.UNAVAILABLE,
        reason_code="dependency_missing",
        message="The scientific engine dependency is not installed.",
        diagnostics={"dependency": "rdkit"},
    )

    assert healthy.status is HealthStatus.HEALTHY
    assert unavailable.status is HealthStatus.UNAVAILABLE


@pytest.mark.parametrize(
    "status",
    (HealthStatus.DEGRADED, HealthStatus.UNAVAILABLE),
)
def test_nonhealthy_reports_require_safe_reason(
    status: HealthStatus,
) -> None:
    with pytest.raises(ValidationError, match="require a safe reason"):
        ScientificEngineHealthReport(status=status)


def test_healthy_reports_reject_failure_details() -> None:
    with pytest.raises(ValidationError, match="cannot carry a failure reason"):
        ScientificEngineHealthReport(
            status=HealthStatus.HEALTHY,
            reason_code="unexpected",
            message="This should not accompany a healthy state.",
        )


def test_protocol_accepts_structural_adapter_implementation() -> None:
    declaration = ScientificEngineAdapterDeclaration(
        adapter_identifier="adapter.fake",
        adapter_version=VERSION,
        engine=ScientificEngineIdentity(
            engine_identifier="engine.fake",
            engine_version="1.0",
        ),
        capabilities=(_envelope("science.fake"),),
    )

    class FakeAdapter:
        @property
        def declaration(self) -> ScientificEngineAdapterDeclaration:
            return declaration

        def health(self) -> ScientificEngineHealthReport:
            return ScientificEngineHealthReport(status=HealthStatus.HEALTHY)

        def invoke(
            self,
            invocation: CapabilityInvocation,
        ) -> CapabilityResult:
            del invocation
            return CapabilityResult(status=ExecutionStatus.SUCCESS)

    assert isinstance(FakeAdapter(), ScientificEngineAdapter)
