"""Production capability-truth and fail-closed planner grounding tests."""

from __future__ import annotations

import pytest

from cgr.pulsate_api.scientific_capability_catalogue import (
    ScientificCapabilityCatalogue,
    ScientificCapabilityDefinition,
    phase8_scientific_capability_catalogue,
)
from cgr.pulsate_api.scientific_capability_truth import (
    ScientificCapabilityGroundingError,
    ScientificCapabilityImplementation,
    ScientificCapabilityTruthCatalogue,
)
from cgr.pulsate_api.scientific_executions import (
    ScientificExecutionRepository,
    ScientificObjectiveCompileRequest,
)
from cgr.pulsate_api.scientific_production import (
    create_scientific_production_composition,
)


def _definition(name: str) -> ScientificCapabilityDefinition:
    return ScientificCapabilityDefinition(
        capability_name=name,
        produced_artifact_types=(f"artifact.{name.replace('.', '-')}",),
        supported_task_types=("bounded_test_task",),
    )


def test_capability_truth_distinguishes_every_implementation_class() -> None:
    names = (
        "science.local",
        "science.external-ready",
        "science.external-unconfigured",
        "science.fallback",
        "science.test-only",
        "science.no-handler",
    )
    catalogue = ScientificCapabilityCatalogue(
        _definition(name) for name in names
    )
    truth = ScientificCapabilityTruthCatalogue.from_runtime(
        catalogue=catalogue,
        registered_capability_names=(*names[:-1], "science.orphan-handler"),
        implementation_overrides={
            "science.external-ready": (
                ScientificCapabilityImplementation.EXTERNAL_EXECUTOR
            ),
            "science.external-unconfigured": (
                ScientificCapabilityImplementation.EXTERNAL_EXECUTOR
            ),
            "science.fallback": (
                ScientificCapabilityImplementation.DETERMINISTIC_FALLBACK
            ),
            "science.test-only": ScientificCapabilityImplementation.TEST_ONLY,
        },
        configured_external_capability_names=("science.external-ready",),
    )

    assert truth.selectable_capability_names() == (
        "science.external-ready",
        "science.fallback",
        "science.local",
    )
    assert truth.get("science.local").implementation is (
        ScientificCapabilityImplementation.PRODUCTION_EXECUTOR
    )
    assert truth.get("science.external-unconfigured").selectable is False
    assert truth.get("science.test-only").selectable is False
    assert truth.get("science.no-handler").implementation is (
        ScientificCapabilityImplementation.UNAVAILABLE
    )
    assert truth.get("science.orphan-handler").declared is False
    assert truth.get("science.orphan-handler").selectable is False

    summary = truth.summary()
    assert summary["available"] is True
    assert summary["selectable_count"] == 3
    assert summary["fingerprint"] == truth.fingerprint


def test_capability_truth_rejects_unavailable_or_test_only_plan_steps() -> None:
    catalogue = ScientificCapabilityCatalogue(
        (_definition("science.local"), _definition("science.test-only"))
    )
    truth = ScientificCapabilityTruthCatalogue.from_runtime(
        catalogue=catalogue,
        registered_capability_names=("science.local", "science.test-only"),
        implementation_overrides={
            "science.test-only": ScientificCapabilityImplementation.TEST_ONLY,
        },
    )

    truth.require_selectable(("science.local",))
    with pytest.raises(
        ScientificCapabilityGroundingError,
        match="science.test-only",
    ):
        truth.require_selectable(("science.local", "science.test-only"))


def test_execution_repository_refuses_to_persist_an_ungrounded_plan(
    tmp_path,
) -> None:
    truth = ScientificCapabilityTruthCatalogue.from_runtime(
        catalogue=phase8_scientific_capability_catalogue(),
        registered_capability_names=(),
    )
    repository = ScientificExecutionRepository(
        tmp_path / "executions",
        capability_truth=truth,
    )
    repository.start()

    with pytest.raises(ScientificCapabilityGroundingError):
        repository.create(
            ScientificObjectiveCompileRequest(
                question=(
                    "Which conformer is preferred in water: axial or equatorial?"
                ),
            )
        )

    assert not tuple((tmp_path / "executions").iterdir())


def test_repository_truth_binding_is_immutable_after_startup(tmp_path) -> None:
    catalogue = ScientificCapabilityCatalogue((_definition("science.local"),))
    truth = ScientificCapabilityTruthCatalogue.from_runtime(
        catalogue=catalogue,
        registered_capability_names=("science.local",),
    )
    repository = ScientificExecutionRepository(tmp_path / "executions")
    assert repository.capability_truth_summary() == {
        "available": False,
        "policy": "unbound_nonproduction_repository",
    }
    repository.bind_capability_truth(truth)
    repository.start()

    assert repository.capability_truth_summary()["fingerprint"] == truth.fingerprint
    with pytest.raises(RuntimeError, match="cannot change"):
        repository.bind_capability_truth(truth)


def test_production_composition_binds_the_actual_runtime_registry(tmp_path) -> None:
    repository = ScientificExecutionRepository(tmp_path / "executions")
    create_scientific_production_composition(
        application_data_root=tmp_path / "runtime",
        execution_repository=repository,
    )

    summary = repository.capability_truth_summary()
    capabilities = {
        item["capability_name"]: item
        for item in summary["capabilities"]
    }
    assert capabilities["scientist.result_assemble"]["selectable"] is True
    assert capabilities["molecular.docking_pose_generate"]["selectable"] is False
    assert capabilities["quantum.ibm_execution_prepare"]["selectable"] is False
    routing = repository.compute_routing_summary()
    assert routing["available"] is True
    assert routing["automatic_selection"] is True
    assert "local_cpu" in routing["available_execution_targets"]
    assert routing["external_submission_requires_authorization"] is True

    repository.start()
    record = repository.create(
        ScientificObjectiveCompileRequest(
            question=(
                "Which conformer is preferred in water: axial or equatorial?"
            ),
        )
    )
    assert record.plan.steps[-1].capability_name == "scientist.result_assemble"
