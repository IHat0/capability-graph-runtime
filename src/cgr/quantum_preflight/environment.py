"""Sanitized evidence about the dedicated Linux scientific runtime."""

from __future__ import annotations

import hashlib
import importlib.metadata
import os
import platform
import re
import socket
import sys
from pathlib import Path
from typing import Any

from .identities import EnvironmentCompatibilityIdentity
from .errors import QuantumDependencyError

DIRECT_VERSIONS = {
    "qiskit": "2.3.1",
    "qiskit-nature": "0.8.0",
    "qiskit-algorithms": "0.4.0",
    "qiskit-aer": "0.17.1",
    "pyscf": "2.13.1",
}
RELEVANT_TRANSITIVE = (
    "numpy", "scipy", "rustworkx", "h5py", "sympy", "psutil", "stevedore"
)
THREAD_POLICY = {
    "PYTHONHASHSEED": "0",
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
}
_CREDENTIAL_NAMES = {
    "IBM_QUANTUM_TOKEN",
    "QISKIT_IBM_TOKEN",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
}


def require_dependencies() -> dict[str, str]:
    """Return exact package versions or one clear domain dependency error."""
    versions: dict[str, str] = {}
    missing: list[str] = []
    mismatched: list[str] = []
    for package, expected in DIRECT_VERSIONS.items():
        try:
            observed = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            missing.append(package)
            continue
        versions[package] = observed
        if observed != expected:
            mismatched.append(f"{package}=={observed} (expected {expected})")
    if missing or mismatched:
        details = []
        if missing:
            details.append("missing: " + ", ".join(sorted(missing)))
        if mismatched:
            details.append("mismatched: " + ", ".join(sorted(mismatched)))
        raise QuantumDependencyError(
            "Dedicated quantum-preflight environment unavailable (" + "; ".join(details) + ")."
        )
    return versions


def network_is_disabled() -> bool:
    """Probe only the non-routable TEST-NET-1 range; no remote service is contacted."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(0.25)
    try:
        return probe.connect_ex(("192.0.2.1", 9)) != 0
    finally:
        probe.close()


def environment_manifest(lock_path: Path, *, image_identifier: str) -> dict[str, Any]:
    versions = require_dependencies()
    transitive = {
        package: importlib.metadata.version(package) for package in RELEVANT_TRANSITIVE
    }
    lock_hash = hashlib.sha256(lock_path.read_bytes()).hexdigest()
    credential_names_present = sorted(_CREDENTIAL_NAMES.intersection(os.environ.keys()))
    manifest = {
        "schema_version": "cgr.quantum-environment/1.0.0",
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "os": platform.system().lower(),
        "architecture": platform.machine().lower(),
        "direct_package_versions": versions,
        "transitive_package_versions": transitive,
        "dependency_lock_sha256": lock_hash,
        "container_image_identifier": image_identifier,
        "thread_limits": {name: os.environ.get(name) for name in THREAD_POLICY},
        "deterministic_seed_policy": "algorithm_globals.random_seed=manifest.random_seed",
        "network_disabled": network_is_disabled(),
        "credential_variable_names_present": credential_names_present,
        "blas_information": _blas_information(),
        "python_major_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
    }
    compatibility = environment_compatibility_identity(manifest)
    manifest["compatibility_identity"] = compatibility.model_dump(mode="json")
    manifest["environment_compatibility_sha256"] = compatibility.fingerprint
    return manifest


def environment_compatibility_identity(
    environment: dict[str, Any],
) -> EnvironmentCompatibilityIdentity:
    """Project stable compatibility fields from complete environment evidence."""
    direct = environment.get("direct_package_versions", {})
    transitive = environment.get("transitive_package_versions", {})
    blas = environment.get("blas_information", {})
    limits = environment.get("thread_limits", {})
    if not all(isinstance(value, dict) for value in (direct, transitive, blas, limits)):
        raise ValueError("Environment compatibility fields must be mappings.")
    return EnvironmentCompatibilityIdentity(
        python_version=str(environment["python_version"]),
        python_implementation=_stable_identifier(environment["python_implementation"]),
        os_family=_stable_identifier(environment["os"]),
        architecture=_stable_identifier(environment["architecture"]),
        direct_package_versions={str(key): str(value) for key, value in direct.items()},
        transitive_package_versions={str(key): str(value) for key, value in transitive.items()},
        dependency_lock_sha256=str(environment["dependency_lock_sha256"]),
        container_image_identifier=str(environment["container_image_identifier"]),
        blas_information={str(key): str(value) for key, value in blas.items()},
        thread_limits={str(key): str(value) for key, value in limits.items()},
        deterministic_seed_policy=_stable_identifier(environment["deterministic_seed_policy"]),
        network_disabled=bool(environment["network_disabled"]),
    )


def _stable_identifier(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9._:/-]+", "_", str(value).strip().lower()).strip("_")
    return normalized or "unknown"


def _blas_information() -> dict[str, Any]:
    try:
        import numpy as np

        configuration = np.__config__.CONFIG
        blas = configuration.get("Build Dependencies", {}).get("blas", {})
        return {key: blas[key] for key in ("name", "version") if key in blas}
    except (ImportError, AttributeError, TypeError):
        return {"status": "unavailable"}
