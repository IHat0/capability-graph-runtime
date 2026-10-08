"""Scientist objectives execute through persisted CGR graph runs."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

from cgr.kernel.contracts import CapabilityVersion
from cgr.pulsate_api.scientific_executions import (
    ScientificExecutionRepository,
    ScientificObjectiveCompileRequest,
)
from cgr.pulsate_api.scientific_objectives import ScientificInputReference
from cgr.pulsate_api.scientific_runtime import (
    ScientificCapabilityFailure,
    ScientificCapabilityOutcome,
    ScientificObjectiveRuntime,
    ScientistCapabilityRegistry,
    ScientistResultAssembler,
    scientific_engine_registry,
)
from cgr.science import ArtifactReference, CreationProvenance
from cgr.electronic_structure import PySCFElectronicStructureAdapter


class PayloadStore:
    def __init__(self) -> None:
        self.payloads = {}

    def read(self, reference):
        return self.payloads[reference.artifact_identifier]

    def write(self, reference, payload):
        self.payloads[reference.artifact_identifier] = bytes(payload)


def test_answer_preserves_new_nominated_docking_refusals_and_independent_reference_scope():
    """Synthetic assembly fixture; not a biological or numerical benchmark."""
    candidate={'candidate_identifier':'synthetic-candidate','identity':{'name':'Synthetic candidate'},
        'candidate_status':'INSUFFICIENT EVIDENCE','assessment_reason':'No qualified transfer',
        'dossier_acquisition':{'missing':[]},'regimen':{'status':'requires_regimen','reason':'No measured exposure'},
        'bioactivity_hypotheses':{'targets':[]},'virtual_organism':None,'functional_models':[]}
    assessment={'candidates':[], 'assumptions':[], 'verification_scope':'Synthetic integrity only',
        'computation_selection':{'reason':'Synthetic classical fixture'}, 'virtual_investigation':{'candidates':[candidate],
        'verification_scope':'Synthetic integrity only','bioactivity_structural':{'screening':{'candidates':[
            {'candidate_identifier':'synthetic-candidate','targets':[
                {'status':'computed','target':{'uniprot_accession':'P00002','pdb_id':'TEST','chain':'A',
                    'independent_pocket_reference':{'reference_chembl_id':'CHEMBL100',
                        'activity_record':{'assay_chembl_id':'CHEMBL300'}}},'docking':{'vina_scores_kcal_per_mol':[-1]}},
                {'status':'unsupported','target':{'uniprot_accession':'P00003','pdb_id':'TST2','chain':'B'},
                    'reason':'Alternate conformation unresolved'}]}]}}}}
    assembler=ScientistResultAssembler()
    assembler._read_json_evidence=lambda record,kind: (assessment,'synthetic-verified-assessment') if kind=='prospective_candidate_assessment' else None
    objective=SimpleNamespace(research_requirements=None,task_type='prospective_candidate_assessment',assumptions=())
    statements=assembler._allowed_statements(objective,SimpleNamespace(evidence_artifact_identifiers=(),verified_scientific_summaries=(),verified=True,limitations=()),(),())
    result=' '.join(s['text'] for s in statements)
    assert 'P00002, PDB TEST chain A: computed Vina scores [-1]' in result
    assert 'P00003, PDB TST2 chain B: not computed. Alternate conformation unresolved' in result
    assert "Its measured activity is not the candidate's activity" in result
    assert all(s['evidence_artifact_identifiers']==['synthetic-verified-assessment'] for s in statements if 'Bioactivity-nominated' in s['text'])


def test_answer_keeps_candidate_binding_predictions_distinct_from_function_and_retains_refusal():
    """Synthetic answer-synthesis fixture, not a candidate computation."""
    candidate={'candidate_identifier':'synthetic-candidate','identity':{'name':'Synthetic candidate'},
        'candidate_status':'INSUFFICIENT EVIDENCE','assessment_reason':'No qualified transfer',
        'dossier_acquisition':{'missing':[]},'regimen':{'status':'requires_regimen','reason':'No measured exposure'},
        'bioactivity_hypotheses':{'targets':[]},'virtual_organism':None,'functional_models':[],
        'target_activity_prediction':{'scope':'Synthetic binding-only research scope','targets':[
            {'status':'predicted','gene':'GENEA','target_accession':'P00001','kind':'Ki','value_umol_l':.1,
                'interval_umol_l':[.01,1.],'interval_definition':'Synthetic marginal interval',
                'limitation':'Nominal binding concentration, not functional inhibition'},
            {'status':'refused','gene':'GENEB','target_accession':'P00002','reason':'Model validation gate failed'}]}}
    assessment={'candidates':[], 'assumptions':[], 'verification_scope':'Synthetic integrity only',
        'computation_selection':{'reason':'Synthetic classical fixture'}, 'virtual_investigation':{'candidates':[candidate],
            'verification_scope':'Synthetic integrity only'}}
    assembler=ScientistResultAssembler()
    assembler._read_json_evidence=lambda record,kind: (assessment,'synthetic-verified-assessment') if kind=='prospective_candidate_assessment' else None
    objective=SimpleNamespace(research_requirements=None,task_type='prospective_candidate_assessment',assumptions=())
    statements=assembler._allowed_statements(objective,SimpleNamespace(evidence_artifact_identifiers=(),verified_scientific_summaries=(),verified=True,limitations=()),(),())
    result=' '.join(s['text'] for s in statements)
    assert 'Predicted candidate Ki for Human GENEA (P00001): 0.1 umol/l' in result
    assert 'marginal interval [0.01, 1]' in result
    assert 'No accepted candidate binding estimate for GENEB (P00002): Model validation gate failed' in result
    assert 'not relabeled as functional IC50' in result
    assert all(s['evidence_artifact_identifiers']==['synthetic-verified-assessment'] for s in statements if 'candidate binding' in s['text'] or 'Predicted candidate' in s['text'])


def test_answer_reports_computed_conditional_human_exposure_without_selecting_a_nominal_case():
    """Synthetic assembly fixture; numerical/biological validation is separate."""
    candidate={'candidate_identifier':'synthetic-candidate','identity':{'name':'Synthetic candidate'},
        'candidate_status':'INSUFFICIENT EVIDENCE','assessment_reason':'Unqualified functional transfer',
        'dossier_acquisition':{'missing':[]},'regimen':{'status':'requires_regimen','reason':'No nominal case'},
        'bioactivity_hypotheses':{'targets':[]},'virtual_organism':None,'functional_models':[],
        'native_sensitivity':{'design':{'limitation':'Conditional levels, not a probability distribution.'},
            'summary':{'qualitative_conclusion':'No clinical conclusion.'},'executions':[
                {'status':'computed','case_identifier':'synthetic-low', 'virtual_organism':{'comparison':{'subjects':[
                    {'species':'Human','subject_identifier':'virtual-person-1',
                        'plasma_metrics':{'cmax_umol_l':.2,'tmax_h':1.,'auc_0_t_umol_h_l':2.}}]}}},
                {'status':'refused','case_identifier':'synthetic-high','reason':'No native output'}]}}
    assessment={'candidates':[], 'assumptions':[], 'verification_scope':'Synthetic integrity only',
        'computation_selection':{'reason':'Synthetic classical fixture'},
        'virtual_investigation':{'candidates':[candidate],'verification_scope':'Synthetic integrity only'}}
    assembler=ScientistResultAssembler()
    assembler._read_json_evidence=lambda record,kind: (assessment,'synthetic-verified-assessment') if kind=='prospective_candidate_assessment' else None
    objective=SimpleNamespace(research_requirements=None,task_type='prospective_candidate_assessment',assumptions=())
    statements=assembler._allowed_statements(objective,SimpleNamespace(evidence_artifact_identifiers=(),verified_scientific_summaries=(),verified=True,limitations=()),(),())
    result=' '.join(s['text'] for s in statements)
    assert 'computed for 1 declared conditional input cases' in result
    assert 'Conditional synthetic-low, Human subject virtual-person-1' in result
    assert 'Cmax 0.2 umol/l, sampled Tmax 1 h, finite-window AUC 2 umol*h/l' in result
    assert 'No nominal case was selected' in result
    assert 'scenario outputs, not measured PK or a nominal prediction' in result
    assert 'Scenario ranges do not establish a clinical confidence interval' in result
    assert 'Human exposure was not manufactured' not in result
    assert 'Conditional synthetic-high' not in result
    assert all(s['evidence_artifact_identifiers']==['synthetic-verified-assessment'] for s in statements if 'Conditional' in s['text'])


def test_answer_displays_exploratory_native_response_without_candidate_decision_authority():
    """Synthetic synthesis contract only; not a Human model qualification."""
    candidate = {'candidate_identifier': 'synthetic-candidate',
        'identity': {'name': 'Synthetic candidate'}, 'candidate_status': 'INSUFFICIENT EVIDENCE',
        'assessment_reason': 'No qualified transfer', 'dossier_acquisition': {'missing': []},
        'regimen': {'status': 'requires_regimen', 'reason': 'Synthetic exposure'},
        'bioactivity_hypotheses': {'targets': []}, 'virtual_organism': None,
        'functional_models': [{'status': 'computed', 'result': {
            'model': {'identifier': 'synthetic-native-model'},
            'perturbation': {'transfer_receipt': {'qualification_state': 'exploratory_only',
                'candidate_decision_authority': False, 'qualification_scope': {
                    'benchmark_discrepancy': 'Synthetic benchmark magnitude criterion failed.',
                    'limitations': ['Native cell response is not clinical QTc.']}}},
            'response': {'V.APD90': {'final_difference': 12.5, 'unit': 'ms'}},
            'limitation': 'Synthetic contract, not a scientific result.'}}]}
    assessment = {'candidates': [], 'assumptions': [], 'verification_scope': 'Synthetic integrity only',
        'computation_selection': {'reason': 'Synthetic classical fixture'},
        'virtual_investigation': {'candidates': [candidate], 'verification_scope': 'Synthetic integrity only'}}
    assembler = ScientistResultAssembler()
    assembler._read_json_evidence = lambda record, kind: (
        (assessment, 'synthetic-verified-assessment') if kind == 'prospective_candidate_assessment' else None)
    objective = SimpleNamespace(research_requirements=None, task_type='prospective_candidate_assessment', assumptions=())
    record = SimpleNamespace(evidence_artifact_identifiers=(), verified_scientific_summaries=(), verified=True, limitations=())
    statements = assembler._allowed_statements(objective, record, (), ())
    result = ' '.join(statement['text'] for statement in statements)
    assert 'Exploratory physiology only' in result
    assert 'cannot independently change candidate status' in result
    assert 'Synthetic benchmark magnitude criterion failed.' in result
    assert 'Native cell response is not clinical QTc.' in result
    assert 'final modeled difference 12.5 ms' in result
    assert all(statement['evidence_artifact_identifiers'] == ['synthetic-verified-assessment']
        for statement in statements if 'physiology' in statement['text'] or 'modeled difference' in statement['text'])


class EvidenceHandler:
    def execute(self, *, invocation, objective, record):
        del objective, record
        payload = invocation.capability_identity.encode()
        reference = ArtifactReference(
            artifact_identifier=(
                "scientific-artifact-"
                + hashlib.sha256(
                    f"{invocation.graph_run_identifier}:{invocation.node_identifier}".encode()
                ).hexdigest()[:32]
            ),
            schema_version=CapabilityVersion(major=1, minor=0, patch=0),
            artifact_type="scientific_execution_evidence",
            media_type="application/json",
            content_sha256=hashlib.sha256(payload).hexdigest(),
            byte_size=len(payload),
            provenance=CreationProvenance(
                producer=invocation.capability_identity,
                producer_version=CapabilityVersion(major=1, minor=0, patch=0),
            ),
        )
        verification = invocation.capability_identity.startswith("scientific_verification.")
        return ScientificCapabilityOutcome(
            output_artifacts=(reference,),
            evidence_artifacts=((reference,) if verification else ()),
            scientific_summary=(
                "The bounded scientific calculation completed with verified evidence."
                if verification else None
            ),
            verified=(True if verification else None),
            scene_identifier=(
                "scientific-scene-runtime" if invocation.capability_identity == "molecular.scene_project" else None
            ),
        )


class RetryOnceHandler(EvidenceHandler):
    def __init__(self) -> None:
        self.attempts: dict[str, int] = {}

    def execute(self, *, invocation, objective, record):
        attempt = self.attempts.get(invocation.node_identifier, 0) + 1
        self.attempts[invocation.node_identifier] = attempt
        if attempt == 1:
            raise ScientificCapabilityFailure(
                "temporary_scientific_failure",
                "The bounded first attempt failed.",
                retryable=True,
            )
        return super().execute(
            invocation=invocation,
            objective=objective,
            record=record,
        )


class ComputationThenVerificationSummaryHandler(EvidenceHandler):
    def execute(self, *, invocation, objective, record):
        outcome = super().execute(
            invocation=invocation,
            objective=objective,
            record=record,
        )
        if invocation.capability_identity.startswith("scientific_verification."):
            return outcome.model_copy(
                update={"scientific_summary": "The blocking verification passed."}
            )
        return outcome.model_copy(
            update={"scientific_summary": "The persisted computation produced result R."}
        )


def test_long_durable_answer_does_not_overflow_the_execution_summary():
    objective = SimpleNamespace(input_references=(), assumptions=(), original_request='Assess a supplied candidate.',
        task_type='protein_ligand_discovery', semantic_target=SimpleNamespace(target_label='target'))
    record = SimpleNamespace(artifact_references=(), node_executions=(), verified=True,
        replanning_event_identifiers=(), evidence_artifact_identifiers=(), scene_identifier=None,
        scientist_summary='Complete.', limitations=())
    assembler = ScientistResultAssembler()
    assembler._allowed_statements = lambda *args: (
        {'statement_identifier': 'short', 'category': 'principal_result', 'text': 'Insufficient evidence.', 'evidence_artifact_identifiers': []},
        {'statement_identifier': 'detail', 'category': 'limitation', 'text': 'Evidence limitation. ' * 500, 'evidence_artifact_identifiers': []},)
    outcome = assembler.execute(invocation=None, objective=objective, record=record)
    assert len(outcome.scientist_result.scientific_result) > 8192
    assert outcome.scientific_summary == 'Insufficient evidence.'


def test_many_candidate_statements_retain_complete_answer():
    objective = SimpleNamespace(input_references=(), assumptions=(), original_request='Assess supplied candidates.',
        task_type='protein_ligand_discovery', semantic_target=SimpleNamespace(target_label='target'))
    record = SimpleNamespace(node_executions=(), artifact_references=(), verified=True,
        scientist_summary='summary', evidence_artifact_identifiers=(), replanning_event_identifiers=(),
        scene_identifier=None, limitations=())
    assembler = ScientistResultAssembler()
    principal = 'Candidate evidence. ' * 600
    follow_up = 'Independent follow-up required. ' * 200
    assembler._allowed_statements = lambda *args: (
        {'category': 'principal_result', 'text': principal, 'evidence_artifact_identifiers': []},
        {'category': 'recommendation', 'text': follow_up, 'evidence_artifact_identifiers': []})
    outcome = assembler.execute(invocation=None, objective=objective, record=record)
    assert principal in outcome.scientist_result.scientific_result
    assert follow_up in outcome.scientist_result.scientific_result
    assert len(outcome.scientist_result.principal_result) <= 8192
    assert len(outcome.scientist_result.recommended_next_step) <= 4096
    assert 'complete answer' in outcome.scientist_result.principal_result


def test_failure_after_verification_preserves_evidence_without_claiming_complete_verification(tmp_path):
    repository = ScientificExecutionRepository(tmp_path / 'executions')
    repository.start()
    version = CapabilityVersion(major=1, minor=0, patch=0)
    artifact = ArtifactReference(artifact_identifier='molecule-artifact', schema_version=version,
        artifact_type='molecular_structure', media_type='chemical/x-mdl-molfile', content_sha256='a' * 64,
        provenance=CreationProvenance(producer='test.fixture', producer_version=version))
    record = repository.create(ScientificObjectiveCompileRequest(
        question='Which conformer is preferred in water: the axial or equatorial form?',
        input_references=(ScientificInputReference(reference_identifier='molecule-input',
            artifact_type='molecular_structure', artifact_identifier=artifact.artifact_identifier),), artifact_references=(artifact,)))
    handler = EvidenceHandler()
    registry = ScientistCapabilityRegistry({s.capability_name: handler for s in record.plan.steps
                                           if s.capability_name not in {'scientist.result_assemble', 'molecular.scene_project'}})
    class FailedScene:
        def execute(self, **kwargs):
            raise ScientificCapabilityFailure('scene_failed', 'Post-verification scene failed.')
    registry.register('molecular.scene_project', FailedScene())
    runtime = ScientificObjectiveRuntime(root=tmp_path / 'workflow', execution_repository=repository, capability_registry=registry)
    runtime.start()
    try:
        failed = runtime.execute(record.execution_identifier)
        assert failed.status == 'failed' and not failed.verified
        assert failed.evidence_artifact_identifiers
        assert failed.scientist_result is None
        assert next(n for n in failed.node_executions if n.capability_name == 'molecular.scene_project').error_code == 'scene_failed'
    finally:
        runtime.close()
        repository.close()


def test_native_adapter_declarations_build_exact_scientist_registry() -> None:
    adapter = PySCFElectronicStructureAdapter(PayloadStore())
    registry = scientific_engine_registry((adapter,))

    assert set(registry.identities()) == {
        envelope.descriptor.capability_name
        for envelope in adapter.declaration.capabilities
    }
    assert registry.get("electronic.implicit_solvent_hartree_fock") is not None


def test_runtime_executes_composed_plan_through_persisted_cgr_graph(tmp_path) -> None:
    repository = ScientificExecutionRepository(tmp_path / "executions")
    repository.start()
    record = repository.create(ScientificObjectiveCompileRequest(
        question="Which conformer is preferred in water: the axial or equatorial form?",
        input_references=(ScientificInputReference(
            reference_identifier="molecule-project",
            artifact_type="molecular_structure",
            artifact_identifier="molecule-artifact",
        ),),
        artifact_references=(ArtifactReference(
            artifact_identifier="molecule-artifact",
            schema_version=CapabilityVersion(major=1, minor=0, patch=0),
            artifact_type="molecular_structure",
            media_type="chemical/x-mdl-molfile",
            content_sha256="a" * 64,
            provenance=CreationProvenance(
                producer="test.fixture",
                producer_version=CapabilityVersion(major=1, minor=0, patch=0),
            ),
        ),),
    ))
    handler = EvidenceHandler()
    registry = ScientistCapabilityRegistry({
        step.capability_name: handler
        for step in record.plan.steps
        if step.capability_name != "scientist.result_assemble"
    })
    runtime = ScientificObjectiveRuntime(
        root=tmp_path / "workflow",
        execution_repository=repository,
        capability_registry=registry,
    )
    runtime.start()

    completed = runtime.execute(record.execution_identifier)
    reloaded = repository.get(record.execution_identifier)

    assert completed == reloaded
    assert completed.status == "succeeded"
    assert completed.verified
    assert completed.scientist_result is not None
    assert completed.scientist_result.verification_status == "passed"
    assert completed.workflow_graph_identifier is not None
    assert completed.workflow_snapshot_fingerprint is not None
    assert all(node.status == "succeeded" for node in completed.node_executions)
    assert completed.scene_identifier == "scientific-scene-runtime"
    assert completed.evidence_artifact_identifiers
    assert "This record contains a plan, not fabricated calculation results." not in (
        completed.limitations
    )
    assert "This record contains a plan" not in (
        completed.scientist_result.scientific_result
    )


def test_verification_promotes_the_preceding_persisted_computation_summary(
    tmp_path,
) -> None:
    repository = ScientificExecutionRepository(tmp_path / "executions")
    repository.start()
    record = repository.create(ScientificObjectiveCompileRequest(
        question="Which conformer is preferred in water: axial or equatorial?",
        input_references=(ScientificInputReference(
            reference_identifier="molecule-project",
            artifact_type="molecular_structure",
            artifact_identifier="molecule-artifact",
        ),),
        artifact_references=(ArtifactReference(
            artifact_identifier="molecule-artifact",
            schema_version=CapabilityVersion(major=1, minor=0, patch=0),
            artifact_type="molecular_structure",
            media_type="chemical/x-mdl-molfile",
            content_sha256="a" * 64,
            provenance=CreationProvenance(
                producer="test.fixture",
                producer_version=CapabilityVersion(major=1, minor=0, patch=0),
            ),
        ),),
    ))
    handler = ComputationThenVerificationSummaryHandler()
    registry = ScientistCapabilityRegistry({
        step.capability_name: handler
        for step in record.plan.steps
        if step.capability_name != "scientist.result_assemble"
    })
    runtime = ScientificObjectiveRuntime(
        root=tmp_path / "workflow",
        execution_repository=repository,
        capability_registry=registry,
    )
    runtime.start()

    completed = runtime.execute(record.execution_identifier)

    assert completed.status == "succeeded"
    assert completed.verified
    assert completed.scientist_result is not None
    assert "The persisted computation produced result R." in (
        completed.verified_scientific_summaries
    )
    assert "The blocking verification passed." in (
        completed.verified_scientific_summaries
    )
    assert "The persisted computation produced result R." in (
        completed.scientist_result.scientific_result
    )


def test_generated_molecular_entity_description_uses_persisted_metadata() -> None:
    reference = ArtifactReference(
        artifact_identifier="generated-molecular-structure",
        schema_version=CapabilityVersion(major=1, minor=0, patch=0),
        artifact_type="molecular_structure",
        media_type="application/json",
        content_sha256="f" * 64,
        metadata={
            "molecular_formula": "AB2",
            "geometry_point_identifier": "geometry-point-001",
        },
        provenance=CreationProvenance(
            producer="test.fixture",
            producer_version=CapabilityVersion(major=1, minor=0, patch=0),
        ),
    )

    description = ScientistResultAssembler._generated_entity_description(reference)

    assert "generated-molecular-structure" in description
    assert "molecular_formula=AB2" in description
    assert "geometry_point_identifier=geometry-point-001" in description


def test_runtime_retries_a_retryable_scientific_capability(tmp_path) -> None:
    repository = ScientificExecutionRepository(tmp_path / "executions")
    repository.start()
    record = repository.create(ScientificObjectiveCompileRequest(
        question="Which conformer is preferred in water: axial or equatorial?",
        input_references=(ScientificInputReference(
            reference_identifier="molecule-project",
            artifact_type="molecular_structure",
            artifact_identifier="molecule-artifact",
        ),),
        artifact_references=(ArtifactReference(
            artifact_identifier="molecule-artifact",
            schema_version=CapabilityVersion(major=1, minor=0, patch=0),
            artifact_type="molecular_structure",
            media_type="chemical/x-mdl-molfile",
            content_sha256="a" * 64,
            provenance=CreationProvenance(
                producer="test.fixture",
                producer_version=CapabilityVersion(major=1, minor=0, patch=0),
            ),
        ),),
    ))
    retrying = RetryOnceHandler()
    registry = ScientistCapabilityRegistry({
        step.capability_name: retrying
        for step in record.plan.steps
        if step.capability_name != "scientist.result_assemble"
    })
    runtime = ScientificObjectiveRuntime(
        root=tmp_path / "workflow",
        execution_repository=repository,
        capability_registry=registry,
    )
    runtime.start()

    completed = runtime.execute(record.execution_identifier)

    assert completed.status == "succeeded"
    assert all(node.status == "succeeded" for node in completed.node_executions)
    assert all(
        node.attempt_count == 2
        for node in completed.node_executions
        if node.capability_name != "scientist.result_assemble"
    )


def test_answer_synthesis_rejects_model_authored_or_omitted_evidence(tmp_path) -> None:
    class Provider:
        provider_kind = "controlled_test_provider"
        model_name = "replaceable-model"

        def complete(self, _messages):
            return '{"answer":"invented result"}'

    repository = ScientificExecutionRepository(tmp_path / "executions")
    repository.start()
    record = repository.create(
        ScientificObjectiveCompileRequest(
            question="Which conformer is preferred in water: axial or equatorial?",
            input_references=(
                ScientificInputReference(
                    reference_identifier="molecule-project",
                    artifact_type="molecular_structure",
                    artifact_identifier="molecule-artifact",
                ),
            ),
            artifact_references=(
                ArtifactReference(
                    artifact_identifier="molecule-artifact",
                    schema_version=CapabilityVersion(major=1, minor=0, patch=0),
                    artifact_type="molecular_structure",
                    media_type="chemical/x-mdl-molfile",
                    content_sha256="a" * 64,
                    metadata={
                        "acquisition_source_kind": "trusted-test-source",
                        "acquisition_source_identifier": "SOURCE-1",
                        "evidence_resolution_confidence": "high",
                    },
                    provenance=CreationProvenance(
                        producer="test.fixture",
                        producer_version=CapabilityVersion(major=1, minor=0, patch=0),
                    ),
                ),
            ),
        )
    )
    handler = EvidenceHandler()
    registry = ScientistCapabilityRegistry(
        {
            step.capability_name: handler
            for step in record.plan.steps
            if step.capability_name != "scientist.result_assemble"
        }
    )
    runtime = ScientificObjectiveRuntime(
        root=tmp_path / "workflow",
        execution_repository=repository,
        capability_registry=registry,
        result_assembler=ScientistResultAssembler(
            provider=Provider(),  # type: ignore[arg-type]
        ),
    )
    runtime.start()

    completed = runtime.execute(record.execution_identifier)

    result = completed.scientist_result
    assert result is not None
    assert "invented result" not in result.scientific_result
    assert result.principal_result == (
        "The bounded scientific calculation completed with verified evidence."
    )
    assert completed.verified_scientific_summaries == (
        "The bounded scientific calculation completed with verified evidence.",
    )
    assert result.evidence_artifact_identifiers
    assert "trusted-test-source" in result.structures_and_entities[0]
