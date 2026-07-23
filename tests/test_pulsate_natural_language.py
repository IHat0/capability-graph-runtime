"""Natural-language scientific interpretation and approval regressions."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_pulsate_runs import ControlledExecutor

from cgr.pulsate_api.app import _load_preset, create_app
from cgr.pulsate_api.experiments import ExperimentStore
from cgr.pulsate_api.natural_language import (
    ApprovalRequest,
    ModelScientificDraft,
    NaturalLanguageInterpretationError,
    NaturalLanguageInterpretationStore,
    OpenAICompatibleModelProvider,
)
from cgr.pulsate_api.runs import RunCoordinator

LIH_QUESTION = (
    "Calculate the ground-state energy of lithium hydride at a bond length "
    "of 1.6 angstrom using STO-3G on IBM Quantum."
)
H2_QUESTION = (
    "What is the electronic ground-state energy of H2 with the nuclei "
    "0.735 angstrom apart? Use the Jordan-Wigner mapper and IBM Quantum."
)
BEH2_QUESTION = (
    "Prepare a ground-state experiment for linear beryllium hydride with "
    "1.33 angstrom Be-H bonds."
)


def _field(value: Any, provenance: str = "explicit") -> dict[str, Any]:
    return {"value": value, "provenance": provenance if value is not None else "missing"}


def _complete_draft(
    *,
    question: str = LIH_QUESTION,
    name: str = "lithium hydride",
    formula: str = "LiH",
    formula_provenance: str = "derived",
    elements: tuple[str, ...] = ("Li", "H"),
    coordinates: tuple[tuple[float, float, float], ...] | None = None,
    distance: float | None = 1.6,
    mapper: str = "jordan_wigner",
    target: str | None = "ibm_quantum",
) -> dict[str, Any]:
    atoms = [
        {
            "element": element,
            "coordinates": list(coordinates[index]) if coordinates else None,
        }
        for index, element in enumerate(elements)
    ]
    bonds = (
        [{"atom_indices": [0, 1], "value": distance, "unit": "angstrom"}]
        if distance is not None
        else None
    )
    evidence: dict[str, str] = {
        "scientific_objective": question,
        "requested_quantity": "ground-state energy",
    }
    for path, explicit_value in (
        ("molecule.name", name),
        ("molecule.formula", formula),
        ("molecule.geometry_description", "linear"),
        ("basis", "STO-3G"),
        ("mapper", "Jordan-Wigner"),
        ("requested_execution_target", "IBM Quantum"),
    ):
        if explicit_value and explicit_value.casefold() in question.casefold():
            start = question.casefold().index(explicit_value.casefold())
            evidence[path] = question[start : start + len(explicit_value)]
    if distance is not None:
        for unit_text in ("angstrom", "Å"):
            candidate = f"{distance} {unit_text}"
            if candidate.casefold() in question.casefold():
                start = question.casefold().index(candidate.casefold())
                quotation = question[start : start + len(candidate)]
                evidence["molecule.bond_lengths"] = question
                evidence["coordinate_unit"] = quotation
                break
    return {
        "schema_version": "cgr.pulsate-model-scientific-draft/1.0.0",
        "scientific_objective": _field("prepare an electronic ground-state experiment"),
        "requested_quantity": _field("ground-state energy"),
        "molecule": {
            "name": _field(name),
            "formula": _field(formula, formula_provenance),
            "smiles": _field(None),
            "inchi": _field(None),
            "atoms": _field(atoms, "explicit" if coordinates else "derived"),
            "geometry_description": _field("linear" if len(elements) > 1 else None),
            "bond_lengths": _field(bonds),
        },
        "coordinate_unit": _field("angstrom"),
        "charge": _field(0, "assumed"),
        "multiplicity": _field(1, "assumed"),
        "basis": _field("sto-3g"),
        "electronic_structure_method": _field("rhf", "assumed"),
        "active_space": _field("2 electrons in 2 spatial orbitals", "assumed"),
        "mapper": _field(mapper),
        "ansatz": _field("uccsd", "assumed"),
        "optimizer": _field("slsqp", "assumed"),
        "tolerance": _field(1e-5, "assumed"),
        "requested_execution_target": _field(target),
        "requested_backend": _field(None),
        "shots": _field(None),
        "precision": _field(0.015, "assumed"),
        "assumptions": (),
        "missing_required_information": (),
        "warnings": (),
        "explicit_evidence": evidence,
    }


class ControlledProvider:
    provider_kind = "controlled_test_provider"
    model_name = "controlled-scientific-model"

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.request_count = 0
        self.messages: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.messages.append(messages)
        index = self.request_count
        self.request_count += 1
        return self.responses[index]


def _interpret(tmp_path: Path, question: str, draft: dict[str, Any]):
    provider = ControlledProvider([json.dumps(draft)])
    store = NaturalLanguageInterpretationStore(tmp_path / "interpretations", provider)
    store.start()
    return store, provider, store.interpret(question)


@pytest.mark.parametrize(
    ("question", "draft", "status", "support"),
    [
        (
            LIH_QUESTION,
            _complete_draft(),
            "ready_for_review",
            "supported",
        ),
        (
            H2_QUESTION,
            _complete_draft(
                question=H2_QUESTION,
                name="molecular hydrogen",
                formula="H2",
                formula_provenance="explicit",
                elements=("H", "H"),
                distance=0.735,
            ),
            "ready_for_review",
            "supported",
        ),
        (
            BEH2_QUESTION,
            _complete_draft(
                question=BEH2_QUESTION,
                name="beryllium hydride",
                formula="BeH2",
                elements=("H", "Be", "H"),
                coordinates=((-1.33, 0.0, 0.0), (0.0, 0.0, 0.0), (1.33, 0.0, 0.0)),
                distance=1.33,
                target=None,
            ),
            "needs_clarification",
            "needs_clarification",
        ),
    ],
)
def test_varied_complete_questions_are_interpreted_without_a_molecule_whitelist(
    tmp_path: Path,
    question: str,
    draft: dict[str, Any],
    status: str,
    support: str,
) -> None:
    store, provider, response = _interpret(tmp_path, question, draft)
    try:
        assert response.original_question == question
        assert response.interpretation_status == status
        assert response.execution_support_status == support
        assert response.model_provenance.request_count_for_interpretation == 1
        assert provider.request_count == 1
        if draft["requested_execution_target"]["value"] is None:
            assert response.specification.requested_execution_target.value == "ibm_quantum"
            assert response.specification.requested_execution_target.provenance == "assumed"
    finally:
        store.close()


@pytest.mark.parametrize(
    ("question", "name"),
    [
        ("Study caffeine on IBM Quantum.", "caffeine"),
        ("Calculate the energy.", None),
    ],
)
def test_incomplete_questions_need_clarification_without_inventing_geometry(
    tmp_path: Path, question: str, name: str | None
) -> None:
    draft = _complete_draft(
        question=question,
        name=name or "placeholder",
        formula="C8H10N4O2",
    )
    draft["molecule"]["name"] = _field(name)
    draft["molecule"]["formula"] = _field(None)
    draft["molecule"]["atoms"] = _field(None)
    draft["molecule"]["bond_lengths"] = _field(None)
    draft["molecule"]["geometry_description"] = _field(None)
    if name is None:
        draft["scientific_objective"] = _field("calculate energy")
    store, _, response = _interpret(tmp_path, question, draft)
    try:
        assert response.interpretation_status == "needs_clarification"
        assert response.execution_support_status == "needs_clarification"
        assert "geometry" in response.missing_required_information
        assert response.specification.molecule.atoms.value is None
        if name is None:
            assert "molecular_identity" in response.missing_required_information
    finally:
        store.close()


def test_diatomic_coordinates_are_deterministically_derived_and_not_invented(
    tmp_path: Path,
) -> None:
    question = "LiH at a bond length of 1.6 angstrom"
    store, _, response = _interpret(
        tmp_path,
        question,
        _complete_draft(
            question=question,
            formula_provenance="explicit",
        ),
    )
    try:
        atoms = response.specification.molecule.atoms
        assert atoms.provenance == "derived"
        assert atoms.value is not None
        assert atoms.value[0].coordinates == (-0.8, 0.0, 0.0)
        assert atoms.value[1].coordinates == (0.8, 0.0, 0.0)
        assert response.specification.coordinate_unit.provenance == "derived"
    finally:
        store.close()


def test_conflicting_formula_and_geometry_evidence_creates_blocking_findings(
    tmp_path: Path,
) -> None:
    question = (
        "H2 atoms H (0, 0, 0) and Li (2.0, 0, 0) with a "
        "1.6 angstrom bond."
    )
    draft = _complete_draft(
        question=question,
        formula="H2",
        formula_provenance="explicit",
        elements=("H", "Li"),
        coordinates=((0.0, 0.0, 0.0), (2.0, 0.0, 0.0)),
        distance=1.6,
    )
    draft["explicit_evidence"]["molecule.atoms"] = (
        "H (0, 0, 0) and Li (2.0, 0, 0)"
    )
    store, _, response = _interpret(tmp_path, question, draft)
    try:
        assert "formula_atom_list_conflict" in response.warnings
        assert "cartesian_bond_length_conflict" in response.warnings
        assert response.scientist_approval_possible is False
        with pytest.raises(ValueError, match="contradictions"):
            store.approve(
                response.interpretation_identifier,
                ApprovalRequest(
                    specification=response.specification,
                    accepted_assumptions=True,
                ),
            )
    finally:
        store.close()


def test_malformed_model_output_receives_only_one_repair_then_fails(tmp_path: Path) -> None:
    provider = ControlledProvider(["not json", '{"still":"wrong"}'])
    store = NaturalLanguageInterpretationStore(tmp_path, provider)
    store.start()
    try:
        with pytest.raises(NaturalLanguageInterpretationError, match="valid scientific draft"):
            store.interpret("Calculate a ground-state energy.")
        assert provider.request_count == 2
        assert "Repair the following malformed draft" in provider.messages[1][1]["content"]
    finally:
        store.close()


def test_unknown_model_structures_and_unsafe_paths_are_rejected() -> None:
    draft = _complete_draft()
    draft["environment"] = {"PULSATE_NL_MODEL_API_KEY": "secret"}
    with pytest.raises(ValidationError):
        ModelScientificDraft.model_validate(draft)
    draft = _complete_draft()
    draft["explicit_evidence"]["environment.PATH"] = "PATH"
    with pytest.raises(ValidationError):
        ModelScientificDraft.model_validate(draft)
    draft = _complete_draft()
    draft["molecule"]["name"] = _field("/tmp/payload")
    with pytest.raises(ValidationError):
        ModelScientificDraft.model_validate(draft)


def test_model_claimed_explicit_values_require_grounded_question_evidence(
    tmp_path: Path,
) -> None:
    question = "Calculate the ground-state energy of an unspecified molecule."
    draft = _complete_draft(question=question)
    draft["charge"] = _field(-3)
    draft["multiplicity"] = _field(5)
    draft["basis"] = _field("invented-basis")
    draft["requested_execution_target"] = _field("ibm_quantum")
    draft["molecule"]["atoms"] = _field(
        [
            {"element": "C", "coordinates": [99.0, 98.0, 97.0]},
            {"element": "O", "coordinates": [96.0, 95.0, 94.0]},
        ]
    )
    draft["explicit_evidence"].update(
        {
            "charge": "charge -3",
            "multiplicity": "multiplicity 5",
            "basis": "invented-basis",
            "requested_execution_target": "IBM Quantum",
            "molecule.atoms": "C 99 98 97 O 96 95 94",
        }
    )
    store, _, response = _interpret(tmp_path, question, draft)
    try:
        specification = response.specification
        assert specification.charge.provenance == "assumed"
        assert specification.multiplicity.provenance == "assumed"
        assert specification.basis.provenance == "assumed"
        assert specification.requested_execution_target.provenance == "assumed"
        assert specification.molecule.atoms.provenance == "missing"
        assert specification.molecule.atoms.value is None
        assert "geometry" in response.missing_required_information
    finally:
        store.close()


def test_invented_objective_and_context_free_numbers_cannot_remain_explicit(
    tmp_path: Path,
) -> None:
    question = (
        "Calculate the ground-state energy with 1000 shots, tolerance 1e-5, "
        "5 optimizer iterations, and a 1.6 angstrom box. The object coordinates "
        "are numbers 1, 2, 3, 4, 5, 6, 7, 8, and 9."
    )
    draft = _complete_draft(question=question)
    draft["scientific_objective"] = _field("optimize drug binding")
    draft["charge"] = _field(1000)
    draft["multiplicity"] = _field(5)
    draft["precision"] = _field(1e-5)
    draft["shots"] = _field(1000)
    draft["tolerance"] = _field(1e-5)
    draft["molecule"]["atoms"] = _field(
        [
            {"element": "C", "coordinates": [1.0, 2.0, 3.0]},
            {"element": "O", "coordinates": [4.0, 5.0, 6.0]},
            {"element": "H", "coordinates": [7.0, 8.0, 9.0]},
        ]
    )
    draft["explicit_evidence"].update(
        {
            "scientific_objective": "Calculate the ground-state energy",
            "charge": "1000 shots",
            "multiplicity": "5 optimizer iterations",
            "precision": "tolerance 1e-5",
            "shots": "1000 shots",
            "tolerance": "tolerance 1e-5",
            "molecule.atoms": (
                "The object coordinates are numbers 1, 2, 3, 4, 5, 6, 7, 8, and 9"
            ),
            "molecule.bond_lengths": "a 1.6 angstrom box",
        }
    )
    store, _, response = _interpret(tmp_path, question, draft)
    try:
        specification = response.specification
        assert specification.scientific_objective.provenance == "missing"
        assert specification.charge.provenance == "assumed"
        assert specification.multiplicity.provenance == "assumed"
        assert specification.precision.provenance == "assumed"
        assert specification.shots.provenance == "explicit"
        assert specification.tolerance.provenance == "explicit"
        assert specification.molecule.atoms.provenance == "missing"
        assert specification.molecule.bond_lengths.provenance == "missing"
    finally:
        store.close()


def test_contextually_labelled_numeric_and_coordinate_evidence_remains_explicit(
    tmp_path: Path,
) -> None:
    question = (
        "Calculate the ground-state energy of LiH with net charge of -1, "
        "multiplicity 3, 1000 shots, convergence tolerance 1e-5, precision "
        "0.01, and bond length 1.6 angstrom using atoms "
        "Li (-0.8, 0, 0) and H (0.8, 0, 0) on IBM Quantum."
    )
    draft = _complete_draft(
        question=question,
        formula_provenance="explicit",
        coordinates=((-0.8, 0.0, 0.0), (0.8, 0.0, 0.0)),
    )
    draft["charge"] = _field(-1)
    draft["multiplicity"] = _field(3)
    draft["shots"] = _field(1000)
    draft["tolerance"] = _field(1e-5)
    draft["precision"] = _field(0.01)
    draft["explicit_evidence"].update(
        {
            "charge": "net charge of -1",
            "multiplicity": "multiplicity 3",
            "shots": "1000 shots",
            "tolerance": "convergence tolerance 1e-5",
            "precision": "precision 0.01",
            "molecule.atoms": "Li (-0.8, 0, 0) and H (0.8, 0, 0)",
        }
    )
    store, _, response = _interpret(tmp_path, question, draft)
    try:
        specification = response.specification
        for field in (
            specification.charge,
            specification.multiplicity,
            specification.shots,
            specification.tolerance,
            specification.precision,
            specification.molecule.atoms,
            specification.molecule.bond_lengths,
        ):
            assert field.provenance == "explicit"
    finally:
        store.close()


def test_approval_requires_assumption_acknowledgement_and_is_immutable(
    tmp_path: Path,
) -> None:
    store, _, response = _interpret(tmp_path, LIH_QUESTION, _complete_draft())
    request = ApprovalRequest(
        specification=response.specification,
        accepted_assumptions=False,
    )
    try:
        with pytest.raises(ValueError, match="assumptions"):
            store.approve(response.interpretation_identifier, request)
        approved = store.approve(
            response.interpretation_identifier,
            request.model_copy(update={"accepted_assumptions": True}),
        )
        record = (
            tmp_path
            / "interpretations"
            / "approved"
            / approved.experiment_identifier
            / "experiment.json"
        )
        assert approved.experiment_identifier.startswith("experiment-")
        assert len(approved.specification_sha256) == 64
        assert approved.requested_execution_target == "ibm_quantum"
        assert approved.status == "ready_for_ibm_submission"
        assert json.loads(record.read_text(encoding="utf-8")) == approved.model_dump(mode="json")
        assert not (tmp_path / "runs").exists()
    finally:
        store.close()


def test_provenance_only_edits_cannot_bypass_assumption_acknowledgement(
    tmp_path: Path,
) -> None:
    store, _, response = _interpret(tmp_path, LIH_QUESTION, _complete_draft())
    try:
        reviewed = response.specification.model_copy(deep=True)
        for field_name in (
            "charge",
            "multiplicity",
            "electronic_structure_method",
            "active_space",
            "ansatz",
            "optimizer",
            "tolerance",
            "precision",
        ):
            field = getattr(reviewed, field_name)
            assert field.provenance == "assumed"
            field.provenance = "explicit"
        with pytest.raises(ValueError, match="assumptions"):
            store.approve(
                response.interpretation_identifier,
                ApprovalRequest(
                    specification=reviewed,
                    accepted_assumptions=False,
                ),
            )
        approved_root = tmp_path / "interpretations" / "approved"
        assert not approved_root.exists() or not any(approved_root.iterdir())
    finally:
        store.close()


def test_approval_rebuilds_assumptions_and_audits_scientist_overrides(
    tmp_path: Path,
) -> None:
    draft = _complete_draft()
    draft["assumptions"] = ("client-or-model-controlled text",)
    store, _, response = _interpret(tmp_path, LIH_QUESTION, draft)
    try:
        assert "client-or-model-controlled text" not in response.assumptions
        reviewed = response.specification.model_copy(deep=True)
        reviewed.assumptions = ()
        with pytest.raises(ValueError, match="assumptions"):
            store.approve(
                response.interpretation_identifier,
                ApprovalRequest(
                    specification=reviewed,
                    accepted_assumptions=False,
                ),
            )

        reviewed.charge.value = 1
        reviewed.charge.provenance = "explicit"
        approved = store.approve(
            response.interpretation_identifier,
            ApprovalRequest(
                specification=reviewed,
                accepted_assumptions=True,
            ),
        )
        assert "charge" in approved.scientist_reviewed_overrides
        assert not any(
            assumption.startswith("charge=")
            for assumption in approved.specification.assumptions
        )
        assert approved.specification.charge.value == 1
        assert approved.specification.charge.provenance == "explicit"
    finally:
        store.close()


def test_approval_blocks_unit_conflicts_and_invalid_bond_indices(
    tmp_path: Path,
) -> None:
    store, _, response = _interpret(tmp_path, LIH_QUESTION, _complete_draft())
    try:
        reviewed = response.specification.model_copy(deep=True)
        reviewed.coordinate_unit.value = "bohr"
        reviewed.coordinate_unit.provenance = "explicit"
        with pytest.raises(ValueError, match="coordinate_unit_conflict"):
            store.approve(
                response.interpretation_identifier,
                ApprovalRequest(
                    specification=reviewed,
                    accepted_assumptions=True,
                ),
            )

        payload = response.specification.model_dump(mode="json")
        payload["molecule"]["bond_lengths"]["value"][0]["atom_indices"] = [0, 99]
        with pytest.raises(ValidationError, match="Bond indices"):
            ApprovalRequest.model_validate(
                {
                    "specification": payload,
                    "accepted_assumptions": True,
                }
            )
    finally:
        store.close()


def test_provider_secret_is_never_returned_or_persisted(tmp_path: Path) -> None:
    provider = ControlledProvider([json.dumps(_complete_draft())])
    provider.secret_for_test = "super-secret-api-key"  # type: ignore[attr-defined]
    store = NaturalLanguageInterpretationStore(tmp_path, provider)
    store.start()
    try:
        response = store.interpret(LIH_QUESTION)
        persisted = (tmp_path / response.interpretation_identifier / "interpretation.json").read_text("utf-8")
        returned = response.model_dump_json()
        assert provider.secret_for_test not in returned  # type: ignore[attr-defined]
        assert provider.secret_for_test not in persisted  # type: ignore[attr-defined]
        assert "api_key" not in returned.casefold()
    finally:
        store.close()


def test_interpretation_and_approval_http_contract_does_not_start_a_run(
    tmp_path: Path,
) -> None:
    provider = ControlledProvider([json.dumps(_complete_draft())])
    natural_store = NaturalLanguageInterpretationStore(
        tmp_path / "interpretations", provider
    )
    experiment_store = ExperimentStore(tmp_path / "legacy-experiments")
    executor = ControlledExecutor()
    coordinator = RunCoordinator(
        run_root=tmp_path / "runs",
        manifest_resolver=_load_preset,
        executor=executor,
        enabled=True,
    )
    with TestClient(
        create_app(
            coordinator=coordinator,
            experiment_store=experiment_store,
            natural_language_store=natural_store,
        )
    ) as client:
        interpreted = client.post(
            "/api/v1/experiments/interpret",
            json={"question": LIH_QUESTION},
        )
        assert interpreted.status_code == 201
        payload = interpreted.json()
        rejected = client.post(
            f"/api/v1/experiments/{payload['interpretation_identifier']}/approve",
            json={
                "specification": payload["specification"],
                "accepted_assumptions": False,
            },
        )
        assert rejected.status_code == 422
        approved = client.post(
            f"/api/v1/experiments/{payload['interpretation_identifier']}/approve",
            json={
                "specification": payload["specification"],
                "accepted_assumptions": True,
            },
        )
        capability = client.get("/api/v1/experiments/interpreter/capability")

    assert approved.status_code == 201
    assert approved.json()["status"] == "ready_for_ibm_submission"
    assert capability.json()["model_request_count"] == 1
    assert executor.calls == 0
    assert list((tmp_path / "runs").glob("run-*")) == []


def test_production_provider_performs_an_openai_compatible_http_request() -> None:
    draft = json.dumps(_complete_draft())
    observed: dict[str, Any] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            observed["path"] = self.path
            observed["authorization"] = self.headers["Authorization"]
            observed["payload"] = json.loads(self.rfile.read(length))
            response = json.dumps(
                {"choices": [{"message": {"content": draft}}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        provider = OpenAICompatibleModelProvider(
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            api_key="test-only-secret",
            model_name="configured-model",
            timeout_seconds=2,
        )
        content = provider.complete(
            [{"role": "user", "content": "Interpret a chemistry question."}]
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert json.loads(content)["basis"]["value"] == "sto-3g"
    assert observed["path"] == "/v1/chat/completions"
    assert observed["authorization"] == "Bearer test-only-secret"
    assert observed["payload"]["model"] == "configured-model"
    assert provider.request_count == 1


def test_real_qwen_acceptance_uses_production_api_and_never_starts_execution() -> None:
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run-pulsate-natural-language-acceptance.sh"
    ).read_text(encoding="utf-8")
    assert 'provider_kind") != "openai_compatible_http"' in script
    assert "/api/v1/experiments/interpret" in script
    assert "/approve" in script
    assert "model_request_count_delta" in script
    assert "model_request_count is zero" not in script
    assert '"$python_bin" -m uvicorn cgr.pulsate_api.app:app' in script
    assert 'PULSATE_INTERPRETATION_ROOT="$interpretation_root"' in script
    assert 'PULSATE_EXPERIMENT_ROOT="$experiment_root"' in script
    assert 'PULSATE_RUN_ROOT="$run_root"' in script
    assert "mktemp -d" in script
    assert "cleanup \"$status\"" in script
    assert "trap - EXIT" in script
    assert "unset PULSATE_IBM_ACKNOWLEDGE_COSTS" in script
    assert "unset PULSATE_IBM_QUANTUM_TOKEN" in script
    assert "unset PULSATE_EXECUTION_ENABLED" in script
    assert "/api/v1/runs" not in script
    assert "run_root_before = snapshot_tree(run_root)" in script
    assert "run_root_after = snapshot_tree(run_root)" in script
    assert "if run_root_after != run_root_before" in script
    assert "default_state_before" in script
    assert "default_state_after" in script
    assert "if default_state_after != default_state_before" in script
    assert "detected_execution_artifacts = execution_artifacts(isolated_root)" in script
    for execution_key in (
        "run_identifier",
        "job_identifier",
        "ibm_job_identifier",
        "execution_identifier",
        "ibm_execution",
        "submission_attempt_identifier",
    ):
        assert f'"{execution_key}"' in script
    for artifact_name in (
        "ibm-worker",
        "quantum-worker",
        "worker-result.json",
        "receipt.json",
        "prepared-submission.json",
        "submitted-job.json",
        "submission-attempt.json",
        "job.json",
        "status.json",
        "local-preflight.json",
    ):
        assert f'"{artifact_name}"' in script
    assert "Fresh Pulsate API log tail (bounded and redacted)" in script
    assert "tail -c 16384" in script
    assert '"ibm_job_submitted": False' not in script
    assert '"local_scientific_calculation_started": False' not in script
    assert "LiH 1.6 angstrom bond evidence was not preserved" in script
    assert "H2 0.735 angstrom separation was not preserved" in script
    assert "The model fabricated caffeine Cartesian geometry" in script
