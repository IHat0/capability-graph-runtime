from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cgr.quantum_candidate.contracts import (
    CandidateArtifactClaim,
    CandidateSandboxPolicy,
)
from cgr.quantum_candidate.protocol import (
    collect_candidate_output,
    public_experiment_document,
    validate_artifact_claim_path,
)
from cgr.quantum_candidate.sandbox import candidate_docker_arguments
from cgr.quantum_candidate.trusted import load_verified_trusted_reference
from cgr.quantum_preflight.manifests import load_manifest

MANIFEST = Path("benchmark-manifests/quantum-preflight/lih-ground-state-v1.json")


def test_candidate_docker_boundary_has_only_three_hardened_mounts(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "experiment.json"
    candidate = tmp_path / "candidate"
    output = tmp_path / "output"
    input_path.write_text("{}", encoding="utf-8")
    candidate.mkdir()
    output.mkdir()
    command = candidate_docker_arguments(
        image_identifier="sha256:" + "a" * 64,
        input_manifest=input_path,
        candidate_directory=candidate,
        output_directory=output,
        policy=CandidateSandboxPolicy(),
        container_name="candidate-test",
    )
    rendered = " ".join(command)
    assert command.count("--mount") == 3
    assert "--network none" in rendered
    assert "--read-only" in command
    assert "--user 10002" in rendered
    assert "--cap-drop ALL" in rendered
    assert "no-new-privileges" in rendered
    assert "docker.sock" not in rendered
    assert "/home" not in rendered
    assert "trusted" not in rendered
    assert "dst=/input/experiment.json,readonly" in rendered
    assert "dst=/candidate,readonly" in rendered
    assert "dst=/output" in rendered


def test_public_input_exposes_no_trusted_answer_or_expectations() -> None:
    manifest = load_manifest(MANIFEST)
    document = public_experiment_document(
        manifest, candidate_dependency_lock_sha256="b" * 64
    )
    rendered = json.dumps(document, sort_keys=True).lower()
    assert "exact_total_energy" not in rendered
    assert "expected_primary_finding" not in rendered
    assert "trusted_reference" not in rendered
    assert document["experiment"] == manifest.experiment.model_dump(mode="json")


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "../reference/receipt.json",
        "https://example.invalid/result",
        "C:\\secret",
    ],
)
def test_unsafe_candidate_artifact_references_are_rejected(path: str) -> None:
    claim = CandidateArtifactClaim(role="candidate_result", path=path)
    assert validate_artifact_claim_path(claim) is False


def test_output_file_count_and_size_quotas_fail_closed(tmp_path: Path) -> None:
    (tmp_path / "one.json").write_text("{}", encoding="utf-8")
    (tmp_path / "two.json").write_text("{}", encoding="utf-8")
    package = collect_candidate_output(
        tmp_path,
        CandidateSandboxPolicy(
            maximum_files=1, maximum_file_bytes=1, maximum_output_bytes=1
        ),
    )
    assert package.findings
    assert {item.code for item in package.findings} == {
        "candidate_output_path_violation"
    }


def test_hostile_output_filename_still_has_a_safe_unique_pointer(
    tmp_path: Path,
) -> None:
    (tmp_path / "---.json").write_text("{}", encoding="utf-8")
    (tmp_path / "___-json").write_text("{}", encoding="utf-8")
    package = collect_candidate_output(tmp_path, CandidateSandboxPolicy())
    identifiers = [item.pointer.artifact_identifier for item in package.files]
    assert len(identifiers) == len(set(identifiers)) == 2
    assert all(identifier.startswith("candidate-output-") for identifier in identifiers)


def test_symbolic_link_output_escape_is_rejected(tmp_path: Path) -> None:
    target = tmp_path.parent / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    link = tmp_path / "escape"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("Symbolic links require host privileges on this platform.")
    package = collect_candidate_output(tmp_path, CandidateSandboxPolicy())
    assert "candidate_output_path_violation" in {item.code for item in package.findings}


def test_candidate_image_definition_contains_no_trusted_code_or_manifests() -> None:
    dockerfile = Path("docker/quantum-candidate/Dockerfile").read_text(encoding="utf-8")
    ignore = Path("docker/quantum-candidate/Dockerfile.dockerignore").read_text(
        encoding="utf-8"
    )
    assert "COPY src" not in dockerfile
    assert "COPY tests" not in dockerfile
    assert "COPY benchmark-manifests" not in dockerfile
    assert "COPY benchmark-fixtures" not in dockerfile
    assert "USER 10002:10002" in dockerfile
    assert ignore.startswith("**\n")
    assert "!requirements/quantum-preflight.lock" in ignore


def test_trusted_reference_is_verified_before_candidate_use(tmp_path: Path) -> None:
    (tmp_path / "receipt.json").write_text(
        '{"payload":{"authorized":true}}', encoding="utf-8"
    )
    experiment = load_manifest(MANIFEST).experiment
    with pytest.raises(ValueError):
        load_verified_trusted_reference(tmp_path, experiment)


def test_host_scripts_require_explicit_network_and_do_not_root_chown() -> None:
    build = Path("scripts/build-quantum-candidate-image.sh").read_text(encoding="utf-8")
    run = Path("scripts/run-quantum-candidate-benchmark.sh").read_text(encoding="utf-8")
    assert build.startswith("#!/usr/bin/env bash\nset -euo pipefail")
    assert run.startswith("#!/usr/bin/env bash\nset -euo pipefail")
    assert "--network none" in build
    assert "--network none" in run
    assert "chown " not in run
    assert 'pipeline_status=("${PIPESTATUS[@]}")' in run
    assert "pipeline_status[0]" in run and "pipeline_status[1]" in run
