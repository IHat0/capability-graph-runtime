from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCIENTIFIC_LOCK_SHA256 = "2513a1f187309b8aab78e087c3009444aaf5c19a783c54ad61c4ca8d1327605f"
HTTP_LOCK_SHA256 = "aa41c475a1d179968b9c32cf6cd89c90630e2935d0e82d24397ebad5c23c11df"


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_generated_python_packaging_metadata_is_untracked_and_ignored() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split("\0")
    generated_metadata = re.compile(
        r"(?:^|/)[^/]+\.(?:egg|dist)-info(?:/|$)|(?:^|/)[^/]+\.pth$"
    )
    assert not [path for path in tracked if generated_metadata.search(path)]

    ignore_rules = source(".gitignore").splitlines()
    assert "*.egg-info/" in ignore_rules
    assert "*.dist-info/" in ignore_rules
    for generated_path in (
        "src/cgr.egg-info/PKG-INFO",
        "src/cgr.dist-info/METADATA",
    ):
        ignored = subprocess.run(
            ["git", "check-ignore", "--no-index", "--quiet", generated_path],
            cwd=ROOT,
            check=False,
        )
        assert ignored.returncode == 0


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
    lock_path = ROOT / "requirements/pulsate-http-integration.lock"
    lock_bytes = lock_path.read_bytes()
    assert hashlib.sha256(lock_bytes).hexdigest() == HTTP_LOCK_SHA256
    lock = lock_bytes.decode("utf-8")
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


def test_source_copying_docker_contexts_exclude_packaging_metadata() -> None:
    required_rules = {
        "**/*.egg-info",
        "**/*.egg-info/**",
        "**/*.dist-info",
        "**/*.dist-info/**",
        "**/*.pth",
    }
    source_copying_contexts: list[Path] = []
    for dockerfile in (ROOT / "docker").glob("*/Dockerfile"):
        dockerfile_source = dockerfile.read_text(encoding="utf-8")
        if re.search(r"(?m)^(?:COPY|ADD)\s+[^\n]*\bsrc(?:/|\s)", dockerfile_source):
            source_copying_contexts.append(dockerfile)
            ignore_file = dockerfile.with_name(f"{dockerfile.name}.dockerignore")
            assert ignore_file.is_file(), f"Missing ignore file for {dockerfile}"
            assert required_rules.issubset(
                set(ignore_file.read_text(encoding="utf-8").splitlines())
            ), f"Packaging metadata can enter the context for {dockerfile}"

    assert source_copying_contexts == [ROOT / "docker/quantum-preflight/Dockerfile"]

    candidate = source("docker/quantum-candidate/Dockerfile")
    candidate_rules = source(
        "docker/quantum-candidate/Dockerfile.dockerignore"
    ).splitlines()
    assert not re.search(r"(?m)^(?:COPY|ADD)\s+[^\n]*\bsrc(?:/|\s)", candidate)
    assert candidate_rules[0] == "**"
    assert not any(rule.startswith("!src") for rule in candidate_rules)


def test_scientific_image_verifies_copied_source_before_dropping_privileges() -> None:
    dockerfile = source("docker/quantum-preflight/Dockerfile")
    copied_source = dockerfile.index("COPY src /app/src")
    copied_project = dockerfile.index("COPY pyproject.toml /app/pyproject.toml")
    metadata_check = dockerfile.index("unexpected_metadata=")
    pip_check = dockerfile.index("python -m pip check")
    final_user = dockerfile.index("USER 10001:10001")

    assert copied_source < copied_project < metadata_check < pip_check < final_user
    assert "find /app/src" in dockerfile
    for pattern in ("*.egg-info", "*.dist-info", "*.pth"):
        assert pattern in dockerfile
    assert "Unexpected Python packaging metadata under /app/src" in dockerfile
    assert dockerfile.rstrip().endswith(
        'ENTRYPOINT ["python", "-m", "cgr.quantum_preflight.cli"]'
    )


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
    base_id_assignment = build.index(
        'base_image_id="$(docker image inspect --format \'{{.Id}}\' "$base_image")"'
    )
    base_integrity_check = build.index("docker run --rm", base_id_assignment)
    derived_build = build.index("docker build \\")
    assert base_id_assignment < base_integrity_check < derived_build
    integrity_block = build[base_integrity_check:derived_build]
    for requirement in (
        "--network none",
        "--read-only",
        "--entrypoint /bin/sh",
        '"$base_image_id"',
        "find /app/src",
        "*.egg-info",
        "*.dist-info",
        "*.pth",
        "python -m pip check",
    ):
        assert requirement in integrity_block
    assert 'source_checkpoint="$(git -C "$repo_root" rev-parse HEAD)"' in build
    assert 'git -C "$repo_root" diff --quiet' in build
    assert 'git -C "$repo_root" diff --cached --quiet' in build
    assert "PULSATE_SOURCE_CHECKPOINT" not in build
    assert "7164aebccdf2b404dd6b1eedaddbadc46e055814" not in build
    assert 'base_image_hex="${base_image_id#sha256:}"' in build
    assert 'pinned_base_image="cgr-quantum-preflight-pinned:${base_image_hex}"' in build
    assert 'docker image tag "$base_image_id" "$pinned_base_image"' in build
    assert 'pinned_base_image_id="$(docker image inspect' in build
    assert '[[ "$pinned_base_image_id" != "$base_image_id" ]]' in build
    assert '--build-arg "BASE_IMAGE=$pinned_base_image"' in build
    assert '--build-arg "BASE_IMAGE=$base_image"' not in build
    assert '--build-arg "BASE_IMAGE=$base_image_id"' not in build
    assert "--pull=false" in build
    assert '--build-arg "BASE_IMAGE_NAME=$base_image"' in build
    assert 'org.opencontainers.image.base.name=$base_image' in build
    assert 'io.pulsate.base.image.id=$base_image_id' in build
    assert 'org.opencontainers.image.revision=$source_checkpoint' in build
    assert 'recorded_base_image_id' in build
    assert 'recorded_source_checkpoint' in build
    assert '[[ "$recorded_base_image_id" != "$base_image_id" ]]' in build
    assert '[[ "$recorded_source_checkpoint" != "$source_checkpoint" ]]' in build
    assert 'post_build_pinned_base_image_id="$(docker image inspect' in build
    assert '[[ "$post_build_pinned_base_image_id" != "$base_image_id" ]]' in build
    assert 'docker image rm "$pinned_base_image"' in build
    assert "trap cleanup EXIT" in build
    assert 'docker image rm "$base_image"' not in build
    assert 'docker image rm "$derived_image"' not in build
    for output in (
        "Source checkpoint", "Base image tag", "Exact base image ID",
        "Temporary pinned local base reference", "Derived image tag",
        "Exact derived image ID",
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
