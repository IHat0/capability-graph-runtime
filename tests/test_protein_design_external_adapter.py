from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from cgr.kernel.contracts import CapabilityVersion, ExecutionContext, ExecutionStatus
from cgr.protein_design import (
    BACKBONE_GENERATE,
    ExternalProteinEngineAdapter,
    ExternalProteinEngineConfiguration,
    configured_protein_design_adapters,
)
from cgr.science import (
    ArtifactPointer,
    ArtifactReference,
    CapabilityInvocation,
    CreationProvenance,
)


class MemoryStore:
    def __init__(self) -> None:
        self.payloads: dict[str, bytes] = {}

    def read(self, reference: ArtifactReference) -> bytes:
        return self.payloads[reference.artifact_identifier]

    def write(self, reference: ArtifactReference, payload: bytes) -> None:
        self.payloads[reference.artifact_identifier] = payload


def _reference(store: MemoryStore) -> ArtifactReference:
    payload = b'{"residue_count":48}'
    digest = hashlib.sha256(payload).hexdigest()
    reference = ArtifactReference(
        artifact_identifier="protein-design-specification-test",
        schema_version=CapabilityVersion(major=1, minor=0, patch=0),
        artifact_type="protein_design_specification",
        media_type="application/json",
        content_sha256=digest,
        byte_size=len(payload),
        provenance=CreationProvenance(producer="test.fixture"),
    )
    store.write(reference, payload)
    return reference


def _runner(path: Path, *, escaped_output: bool = False) -> None:
    output_expression = (
        "Path(request['output_directory']).parent.parent / 'escaped.pdb'"
        if escaped_output
        else "Path(request['output_directory']) / 'candidate-01.pdb'"
    )
    path.write_text(
        "\n".join(
            (
                "import argparse, json",
                "from pathlib import Path",
                "parser = argparse.ArgumentParser()",
                "parser.add_argument('--request', required=True)",
                "parser.add_argument('--manifest', required=True)",
                "args = parser.parse_args()",
                "request = json.loads(Path(args.request).read_text())",
                f"output = {output_expression}",
                "output.write_text('ATOM      1  CA  ALA A   1       0.000   0.000   0.000\\nEND\\n')",
                "manifest = {'outputs': [{'artifact_type': 'protein_backbone_candidate', 'media_type': 'chemical/x-pdb', 'path': str(output), 'parent_artifact_identifiers': [request['inputs'][0]['artifact_identifier']], 'metadata': {'candidate_rank': 1}}], 'diagnostics': {'candidate_count': 1}}",
                "Path(args.manifest).write_text(json.dumps(manifest))",
            )
        ),
        encoding="utf-8",
    )


def _adapter(store: MemoryStore, runner: Path) -> ExternalProteinEngineAdapter:
    return ExternalProteinEngineAdapter(
        store,
        ExternalProteinEngineConfiguration(
            adapter_identifier="adapter.test.protein_backbone",
            engine_identifier="engine.test_protein_backbone",
            engine_version="1.2.3",
            capability_name=BACKBONE_GENERATE,
            command=(sys.executable, str(runner)),
            accepted_artifact_types=("protein_design_specification",),
            produced_artifact_types=("protein_backbone_candidate",),
            timeout_seconds=30,
        ),
    )


def _invocation(
    adapter: ExternalProteinEngineAdapter,
    source: ArtifactReference,
) -> CapabilityInvocation:
    return CapabilityInvocation(
        capability=adapter.declaration.capabilities[0].descriptor,
        input_artifacts=(source,),
        experiment=ArtifactPointer(
            artifact_identifier="protein-design-experiment",
            content_sha256="0" * 64,
        ),
        context=ExecutionContext(execution_id="protein-design-execution"),
        parameters={"random_seed": 17},
    )


def test_external_protein_adapter_hashes_outputs_and_records_lineage(
    tmp_path: Path,
) -> None:
    store = MemoryStore()
    runner = tmp_path / "runner.py"
    _runner(runner)
    adapter = _adapter(store, runner)
    source = _reference(store)

    result = adapter.invoke(_invocation(adapter, source))

    assert result.status is ExecutionStatus.SUCCESS
    assert len(result.output_artifacts) == 1
    output = result.output_artifacts[0]
    assert output.artifact_type == "protein_backbone_candidate"
    assert output.parents == (source.pointer,)
    assert output.metadata["engine_identifier"] == "engine.test_protein_backbone"
    assert result.execution_evidence is not None
    assert result.execution_evidence.details["engine_version"] == "1.2.3"
    assert result.lineage[0].source == source.pointer
    assert store.read(output).startswith(b"ATOM")


def test_external_protein_adapter_rejects_output_path_escape(tmp_path: Path) -> None:
    store = MemoryStore()
    runner = tmp_path / "escaping-runner.py"
    _runner(runner, escaped_output=True)
    adapter = _adapter(store, runner)

    result = adapter.invoke(_invocation(adapter, _reference(store)))

    assert result.status is ExecutionStatus.FAILED
    assert result.failure is not None
    assert result.failure.code == "external_engine_invalid_result"


def test_environment_factory_requires_complete_pinned_configuration() -> None:
    store = MemoryStore()
    assert configured_protein_design_adapters(store, {}) == ()

    try:
        configured_protein_design_adapters(
            store,
            {"PULSATE_RFDIFFUSION_VERSION": "1.0"},
        )
    except ValueError as error:
        assert "must be configured together" in str(error)
    else:
        raise AssertionError("Partial external engine configuration was accepted.")


def test_environment_factory_identifies_the_three_replaceable_engines() -> None:
    store = MemoryStore()
    values = {}
    for prefix, version in (
        ("PULSATE_RFDIFFUSION", "1.1"),
        ("PULSATE_PROTEINMPNN", "1.2"),
        ("PULSATE_BOLTZ", "2.2.1"),
    ):
        values[prefix + "_COMMAND_JSON"] = json.dumps([sys.executable, "runner.py"])
        values[prefix + "_VERSION"] = version

    adapters = configured_protein_design_adapters(store, values)

    assert tuple(
        item.declaration.engine.engine_identifier for item in adapters
    ) == (
        "engine.rfdiffusion",
        "engine.proteinmpnn",
        "engine.boltz",
    )


def test_environment_factory_requires_an_explicit_supported_compute_target() -> None:
    store = MemoryStore()
    values = {
        "PULSATE_RFDIFFUSION_COMMAND_JSON": json.dumps(
            [sys.executable, "hpc-dispatcher.py"]
        ),
        "PULSATE_RFDIFFUSION_VERSION": "1.1",
        "PULSATE_RFDIFFUSION_EXECUTION_TARGET": "hpc_batch",
    }

    adapter = configured_protein_design_adapters(store, values)[0]
    assert adapter.configuration.execution_target == "hpc_batch"
    assert adapter.declaration.capabilities[0].execution_targets == ("hpc_batch",)

    values["PULSATE_RFDIFFUSION_EXECUTION_TARGET"] = "mystery_cluster"
    try:
        configured_protein_design_adapters(store, values)
    except ValueError as error:
        assert "local_gpu or hpc_batch" in str(error)
    else:
        raise AssertionError("An unsupported compute target was accepted.")
