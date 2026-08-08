"""Durable scientist-facing objective and plan records."""

from __future__ import annotations

import json

import pytest

from cgr.pulsate_api.scientific_executions import (
    ScientificExecutionRepository,
    ScientificObjectiveCompileRequest,
)
from cgr.pulsate_api.scientific_objectives import ScientificInputReference


def _protein() -> ScientificInputReference:
    return ScientificInputReference(
        reference_identifier="protein-1",
        artifact_type="protein_structure",
        artifact_identifier="protein-artifact-1",
    )


def test_repository_persists_and_reloads_a_plan_without_claiming_execution(tmp_path) -> None:
    repository = ScientificExecutionRepository(tmp_path / "science")
    repository.start()
    request = ScientificObjectiveCompileRequest(
        question="Discover and optimize a drug candidate for this protein.",
        input_references=(_protein(),),
    )

    created = repository.create(request)
    loaded = repository.get(created.execution_identifier)

    assert loaded == created
    assert loaded.status == "planned"
    assert not loaded.verified
    assert not loaded.result_artifact_identifiers
    assert "not fabricated" in loaded.limitations[0]
    assert repository.create(request) == created


def test_repository_persists_ibm_request_at_authorization_boundary(tmp_path) -> None:
    repository = ScientificExecutionRepository(tmp_path / "science")
    repository.start()

    record = repository.create(ScientificObjectiveCompileRequest(
        question="Study the metal active site of this protein with VQE on IBM Quantum.",
        input_references=(_protein(),),
    ))

    assert record.status == "authorization_required"
    assert not record.plan.ibm_job_submission_planned
    assert not record.objective.ibm_submission_authorized


def test_repository_rejects_tampered_record_identity(tmp_path) -> None:
    repository = ScientificExecutionRepository(tmp_path / "science")
    repository.start()
    record = repository.create(ScientificObjectiveCompileRequest(
        question="Discover and optimize a drug candidate for this protein.",
        input_references=(_protein(),),
    ))
    path = tmp_path / "science" / record.execution_identifier / "record.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["execution_identifier"] = "scientific-execution-" + "0" * 32
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(KeyError):
        repository.get(record.execution_identifier)
