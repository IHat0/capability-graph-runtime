from __future__ import annotations

import hashlib
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCIENTIFIC_LOCK_SHA256 = "2513a1f187309b8aab78e087c3009444aaf5c19a783c54ad61c4ca8d1327605f"


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_scientific_lock_is_unchanged_by_http_layer() -> None:
    lock = (ROOT / "requirements/quantum-preflight.lock").read_bytes()
    assert hashlib.sha256(lock).hexdigest() == SCIENTIFIC_LOCK_SHA256
    for package in ("fastapi", "starlette", "httpx", "uvicorn"):
        assert not re.search(rf"(?mi)^{re.escape(package)}==", lock.decode("utf-8"))


def test_http_input_has_only_the_declared_application_dependencies() -> None:
    requirements = {
        line.strip() for line in source("requirements/pulsate-http-integration.in").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert requirements == {
        "fastapi==0.139.0",
        "httpx==0.28.1",
        "uvicorn==0.51.0",
    }


def test_http_lock_is_exact_and_hash_locked_without_scientific_or_swe_packages() -> None:
    lock = source("requirements/pulsate-http-integration.lock")
    requirement_starts = list(re.finditer(r"(?m)^([A-Za-z0-9_.-]+)==([^ \\\n]+)", lock))
    assert requirement_starts
    for index, match in enumerate(requirement_starts):
        end = requirement_starts[index + 1].start() if index + 1 < len(requirement_starts) else len(lock)
        assert "--hash=sha256:" in lock[match.start():end]
    packages = {match.group(1).lower().replace("_", "-") for match in requirement_starts}
    assert not packages.intersection({
        "qiskit", "qiskit-nature", "pyscf", "swe-rex", "pipx", "pexpect",
        "bashlex", "python-multipart", "rich", "requests",
    })
    assert "pydantic==2.13.4" in lock


def test_derived_dockerfile_preserves_scientific_base_and_user_boundary() -> None:
    dockerfile = source("docker/pulsate-http-integration/Dockerfile")
    assert "ARG BASE_IMAGE=cgr-quantum-preflight:1.0.0" in dockerfile
    assert "FROM ${BASE_IMAGE}" in dockerfile
    assert "ARG SOURCE_CHECKPOINT=unknown" in dockerfile
    assert "7164aebccdf2b404dd6b1eedaddbadc46e055814" not in dockerfile
    assert "org.opencontainers.image.base.name" in dockerfile
    assert "io.pulsate.base.image.id" in dockerfile
    assert "org.opencontainers.image.base.digest" not in dockerfile
    assert "USER root" in dockerfile
    assert "requirements/pulsate-http-integration.lock" in dockerfile
    assert "python -m pip install --no-cache-dir --require-hashes" in dockerfile
    assert "python -m pip check" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert dockerfile.index("USER root") < dockerfile.index("USER 10001:10001")
    assert "apt-get" not in dockerfile and "apk add" not in dockerfile
    assert 'ENTRYPOINT ["python", "-m"]' in dockerfile


def test_derived_build_context_is_a_minimal_allowlist() -> None:
    rules = source("docker/pulsate-http-integration/Dockerfile.dockerignore").splitlines()
    assert rules == [
        "**",
        "!requirements/",
        "!requirements/pulsate-http-integration.lock",
    ]
    assert not any(
        token in "\n".join(rules)
        for token in ("secret", ".env", "node_modules", "result", ".venv", "*.pem")
    )


def test_build_script_pins_provenance_and_verifies_derived_labels() -> None:
    build = source("scripts/build-pulsate-http-integration-image.sh")
    assert "set -euo pipefail" in build
    assert 'cgr-quantum-preflight:1.0.0' in build
    assert 'cgr-pulsate-http-integration:1.0.0' in build
    assert 'build-quantum-preflight-image.sh' in build
    assert 'source_checkpoint="$(git -C "$repo_root" rev-parse HEAD)"' in build
    assert 'git -C "$repo_root" diff --quiet' in build
    assert 'git -C "$repo_root" diff --cached --quiet' in build
    assert "PULSATE_SOURCE_CHECKPOINT" not in build
    assert "7164aebccdf2b404dd6b1eedaddbadc46e055814" not in build
    assert '--build-arg "BASE_IMAGE=$base_image_id"' in build
    assert '--build-arg "BASE_IMAGE=$base_image"' not in build
    assert '--build-arg "BASE_IMAGE_NAME=$base_image"' in build
    assert 'org.opencontainers.image.base.name=$base_image' in build
    assert 'io.pulsate.base.image.id=$base_image_id' in build
    assert 'org.opencontainers.image.revision=$source_checkpoint' in build
    assert 'recorded_base_image_id' in build
    assert 'recorded_source_checkpoint' in build
    assert '[[ "$recorded_base_image_id" != "$base_image_id" ]]' in build
    assert '[[ "$recorded_source_checkpoint" != "$source_checkpoint" ]]' in build
    for output in (
        "Source checkpoint", "Base image tag", "Exact base image ID",
        "Derived image tag", "Exact derived image ID",
    ):
        assert output in build


def test_run_script_uses_derived_identity_and_full_sandbox_contract() -> None:
    run = source("scripts/run-pulsate-http-integration.sh")
    assert "set -euo pipefail" in run
    assert "cgr-pulsate-http-integration:1.0.0" in run
    assert 'derived_image_id="$(docker image inspect' in run
    assert 'PULSATE_QUANTUM_IMAGE_IDENTIFIER=$derived_image_id' in run
    assert "tests/test_pulsate_runs_integration.py" in run
    assert "-p no:cacheprovider" in run
    for restriction in (
        "--network none", "--read-only", "--cpus 2", "--memory 4g",
        "--pids-limit 256", "--security-opt no-new-privileges", "--cap-drop ALL",
        "--tmpfs /tmp:rw,nosuid,nodev,size=1g,mode=1777", "--rm",
    ):
        assert restriction in run
    for package in (
        "fastapi", "starlette", "httpx", "uvicorn", "pytest", "pydantic",
        "qiskit", "qiskit_nature", "pyscf",
    ):
        assert package in run
    assert "pip check" in run


def test_documentation_uses_only_the_derived_integration_command() -> None:
    documentation = source("docs/architecture/pulsate-run-api.md")
    assert "bash ./scripts/run-pulsate-http-integration.sh" in documentation
    assert "cgr-pulsate-http-integration:1.0.0" in documentation
    assert "PULSATE_QUANTUM_IMAGE_IDENTIFIER" in documentation
    assert "--entrypoint python cgr-quantum-preflight:1.0.0" not in documentation
    assert "tests/test_pulsate_runs_integration.py" not in documentation
