from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from cgr.protein_design import (
    BACKBONE_GENERATE,
    SEQUENCE_DESIGN,
    STRUCTURE_PREDICT,
    ExternalProteinEngineAdapter,
    ExternalProteinEngineConfiguration,
)
from cgr.pulsate_api.phase8_scientific_handlers import protein_design_registry
from cgr.pulsate_api.research_sessions import (
    ResearchSessionController,
    ResearchSessionCreateRequest,
    ResearchSessionRepository,
)
from cgr.pulsate_api.scientific_conversation import (
    DeterministicScientificQuestionWriter,
)
from cgr.pulsate_api.scientific_executions import (
    ScientificExecutionRepository,
    ScientificObjectiveCompileRequest,
)
from cgr.pulsate_api.scientific_requirements import (
    ProviderNeutralScientificRequirementInterpreter,
    ScientificRequirement,
    ScientificRequirementProposal,
    validate_requirement_proposal,
)
from cgr.pulsate_api.scientific_runtime import ScientificObjectiveRuntime
from cgr.science import ArtifactReference


class MemoryPayloadStore:
    def __init__(self) -> None:
        self.payloads: dict[str, bytes] = {}

    def read(self, reference: ArtifactReference) -> bytes:
        return self.payloads[reference.artifact_identifier]

    def write(self, reference: ArtifactReference, payload: bytes) -> None:
        self.payloads[reference.artifact_identifier] = payload


def _runner(path: Path) -> None:
    path.write_text(
        r'''import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--request", required=True)
parser.add_argument("--manifest", required=True)
args = parser.parse_args()
request = json.loads(Path(args.request).read_text(encoding="utf-8"))
capability = request["capability_name"]
inputs = request["inputs"]
output_root = Path(request["output_directory"])

def pdb_payload(count):
    lines = []
    for residue in range(1, count + 1):
        lines.append(
            f"ATOM  {residue:5d}  CA  ALA A{residue:4d}    "
            f"{float(residue):8.3f}{0.0:8.3f}{0.0:8.3f}"
            "  1.00  0.00           C  "
        )
    return "\n".join(lines) + "\nEND\n"

if capability == "protein.backbone_generate":
    specification = json.loads(Path(inputs[0]["path"]).read_text())
    count = specification["residue_count"]
    output = output_root / "backbone-01.pdb"
    output.write_text(pdb_payload(count))
    outputs = [{
        "artifact_type": "protein_backbone_candidate",
        "media_type": "chemical/x-pdb",
        "path": str(output),
        "parent_artifact_identifiers": [inputs[0]["artifact_identifier"]],
        "metadata": {"candidate_rank": 1},
    }]
elif capability == "protein.sequence_design":
    pdb = Path(inputs[0]["path"]).read_text().splitlines()
    count = len({line[21:26] for line in pdb if line.startswith("ATOM  ")})
    output = output_root / "sequence-01.fasta"
    output.write_text(">candidate-01\n" + "A" * count + "\n")
    outputs = [{
        "artifact_type": "protein_sequence_candidate",
        "media_type": "text/x-fasta",
        "path": str(output),
        "parent_artifact_identifiers": [inputs[0]["artifact_identifier"]],
        "metadata": {"candidate_rank": 1},
    }]
elif capability == "protein.structure_predict":
    sequence = "".join(
        line.strip()
        for line in Path(inputs[0]["path"]).read_text().splitlines()
        if not line.startswith(">")
    )
    structure = output_root / "predicted-01.pdb"
    confidence = output_root / "confidence-01.json"
    structure.write_text(pdb_payload(len(sequence)))
    confidence.write_text(json.dumps({
        "metric_name": "controlled_test_confidence",
        "confidence_score": 0.81,
    }))
    parent = [inputs[0]["artifact_identifier"]]
    outputs = [
        {
            "artifact_type": "predicted_protein_structure",
            "media_type": "chemical/x-pdb",
            "path": str(structure),
            "parent_artifact_identifiers": parent,
            "metadata": {"candidate_rank": 1},
        },
        {
            "artifact_type": "protein_prediction_confidence",
            "media_type": "application/json",
            "path": str(confidence),
            "parent_artifact_identifiers": parent,
            "metadata": {"candidate_rank": 1},
        },
    ]
else:
    raise SystemExit(2)

Path(args.manifest).write_text(json.dumps({
    "outputs": outputs,
    "diagnostics": {"controlled_test_engine": True},
}))
''',
        encoding="utf-8",
    )


def _adapter(
    store: MemoryPayloadStore,
    runner: Path,
    *,
    capability_name: str,
    accepted: tuple[str, ...],
    produced: tuple[str, ...],
) -> ExternalProteinEngineAdapter:
    return ExternalProteinEngineAdapter(
        store,
        ExternalProteinEngineConfiguration(
            adapter_identifier="adapter.test." + capability_name.replace(".", "_"),
            engine_identifier="engine.test_" + capability_name.replace(".", "_"),
            engine_version="1.0",
            capability_name=capability_name,
            command=(sys.executable, str(runner)),
            accepted_artifact_types=accepted,
            produced_artifact_types=produced,
            timeout_seconds=30,
        ),
    )


def _requirements():
    return validate_requirement_proposal(
        ScientificRequirementProposal(
            proposal_identifier="requirement-proposal-protein-runtime",
            requirements=(
                ScientificRequirement(
                    operation="generate_protein_candidates",
                    requested_output="generated_protein_candidates",
                    supporting_quote="Generate a de novo protein",
                ),
            ),
            summary="Generate a computational protein candidate.",
            provider_kind="controlled_provider",
            model_name="replaceable-model",
        ),
        available_input_types=(),
    )


def test_three_external_engines_execute_one_verified_protein_candidate(
    tmp_path: Path,
) -> None:
    store = MemoryPayloadStore()
    runner = tmp_path / "protein-runner.py"
    _runner(runner)
    adapters = (
        _adapter(
            store,
            runner,
            capability_name=BACKBONE_GENERATE,
            accepted=("protein_design_specification",),
            produced=("protein_backbone_candidate",),
        ),
        _adapter(
            store,
            runner,
            capability_name=SEQUENCE_DESIGN,
            accepted=("protein_backbone_candidate",),
            produced=("protein_sequence_candidate",),
        ),
        _adapter(
            store,
            runner,
            capability_name=STRUCTURE_PREDICT,
            accepted=("protein_sequence_candidate",),
            produced=(
                "predicted_protein_structure",
                "protein_prediction_confidence",
            ),
        ),
    )
    repository = ScientificExecutionRepository(tmp_path / "executions")
    repository.start()
    record = repository.create(
        ScientificObjectiveCompileRequest(
            question="Generate a de novo protein with exactly 20 residues.",
            research_requirements=_requirements(),
        )
    )
    runtime = ScientificObjectiveRuntime(
        root=tmp_path / "workflow",
        execution_repository=repository,
        capability_registry=protein_design_registry(
            store=store,
            adapters=adapters,
        ),
    )
    runtime.start()

    completed = runtime.execute(record.execution_identifier)

    assert completed.status == "succeeded"
    assert completed.verified is True
    assert completed.scene_identifier is not None
    artifact_types = {item.artifact_type for item in completed.artifact_references}
    assert {
        "protein_design_specification",
        "protein_backbone_candidate",
        "protein_sequence_candidate",
        "predicted_protein_structure",
        "protein_prediction_confidence",
        "protein_design_verification_report",
        "scientific_verification_report",
        "molecular_scene_state",
    }.issubset(artifact_types)
    verification = next(
        item
        for item in completed.artifact_references
        if item.artifact_type == "protein_design_verification_report"
    )
    report = json.loads(store.read(verification))
    assert report["lineage_complete"] is True
    assert report["structure_prediction_is_experimental_evidence"] is False


def test_one_complete_question_runs_design_verification_answer_and_scene(
    tmp_path: Path,
) -> None:
    class Provider:
        provider_kind = "controlled_test_provider"
        model_name = "replaceable-model"

        def complete(self, _messages: list[dict[str, str]]) -> str:
            return json.dumps(
                {
                    "requirements": [
                        {
                            "operation": "generate_protein_candidates",
                            "requested_output": "generated_protein_candidates",
                            "supporting_quote": "Generate a de novo protein",
                        }
                    ]
                }
            )

    store = MemoryPayloadStore()
    runner = tmp_path / "protein-runner.py"
    _runner(runner)
    adapters = (
        _adapter(
            store,
            runner,
            capability_name=BACKBONE_GENERATE,
            accepted=("protein_design_specification",),
            produced=("protein_backbone_candidate",),
        ),
        _adapter(
            store,
            runner,
            capability_name=SEQUENCE_DESIGN,
            accepted=("protein_backbone_candidate",),
            produced=("protein_sequence_candidate",),
        ),
        _adapter(
            store,
            runner,
            capability_name=STRUCTURE_PREDICT,
            accepted=("protein_sequence_candidate",),
            produced=(
                "predicted_protein_structure",
                "protein_prediction_confidence",
            ),
        ),
    )
    execution_repository = ScientificExecutionRepository(tmp_path / "executions")
    session_repository = ResearchSessionRepository(tmp_path / "sessions")
    execution_repository.start()
    session_repository.start()
    runtime = ScientificObjectiveRuntime(
        root=tmp_path / "workflow",
        execution_repository=execution_repository,
        capability_registry=protein_design_registry(
            store=store,
            adapters=adapters,
        ),
    )
    runtime.start()
    provider = Provider()
    controller = ResearchSessionController(
        repository=session_repository,
        execution_repository=execution_repository,
        question_writer=DeterministicScientificQuestionWriter(),
        requirement_interpreter=ProviderNeutralScientificRequirementInterpreter(
            provider  # type: ignore[arg-type]
        ),
        runtime=runtime,
    )
    tenant = hashlib.sha256(b"tenant-one-question-runtime").hexdigest()

    planned = controller.create(
        ResearchSessionCreateRequest(
            question="Generate a de novo protein with exactly 20 residues."
        ),
        tenant_identifier_sha256=tenant,
    )
    completed = controller.execute(
        planned.session_identifier,
        tenant_identifier_sha256=tenant,
    )

    assert planned.status == "planned"
    assert planned.next_questions == ()
    assert completed.status == "completed"
    assert completed.scientist_result is not None
    assert completed.scientist_result.verification_status == "passed"
    assert completed.scientist_result.methods
    assert completed.scientist_result.principal_result
    assert completed.scientist_result.confidence_and_uncertainty
    assert completed.scientist_result.recommended_next_step
    assert completed.scene_identifier is not None
