"""Run-bound trusted preflight coordinator for a Docker --network none container."""

from __future__ import annotations

import argparse
import os
import re
import signal
import threading
import time
from pathlib import Path
from typing import Any

from cgr.quantum_preflight.artifacts import write_json_atomic
from cgr.quantum_preflight.contracts import ManifestEnvelope
from cgr.science import sha256_fingerprint

from .ibm import (
    IBM_PREFLIGHT_HANDOFF_SCHEMA,
    IBM_PREFLIGHT_LAUNCHER_SCHEMA,
    IBMRunBoundPreflightRequest,
)
from .runs import ExistingQuantumPreflightExecutor, _controlled_json

_MAXIMUM_BYTES = 2 * 1024 * 1024
_RUN_IDENTIFIER = re.compile(r"run-[0-9a-f]{32}")


def _validated_directory(path: Path, *, parent: Path | None = None) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise ValueError("IBM preflight coordinator directory is invalid.")
    resolved = path.resolve(strict=True)
    if parent is not None and resolved.parent != parent:
        raise ValueError("IBM preflight coordinator directory escaped its root.")
    return resolved


def _assert_no_network_boundary() -> None:
    if os.name != "posix":
        raise RuntimeError("The isolated preflight coordinator requires POSIX.")
    interfaces = Path("/sys/class/net")
    if not interfaces.is_dir():
        raise RuntimeError("The container network namespace cannot be verified.")
    observed = {entry.name for entry in interfaces.iterdir()}
    if observed != {"lo"}:
        raise RuntimeError("The preflight container is not isolated by --network none.")


def _assert_no_ibm_credentials() -> None:
    forbidden = (
        "PULSATE_IBM_QUANTUM_TOKEN",
        "PULSATE_IBM_QUANTUM_INSTANCE",
        "PULSATE_IBM_QUANTUM_BACKEND",
        "PULSATE_IBM_ACKNOWLEDGE_COSTS",
    )
    if any(os.environ.get(name) for name in forbidden):
        raise RuntimeError("IBM authority must not enter the preflight coordinator.")


def _readiness(
    *,
    scientific_image_identifier: str,
    ibm_runtime_image_identifier: str,
) -> dict[str, Any]:
    return {
        "schema_version": IBM_PREFLIGHT_LAUNCHER_SCHEMA,
        "launcher_mode": "run_bound_file_coordinator",
        "network_boundary": "docker_network_none",
        "network_disabled": True,
        "coordinator_process_identifier": os.getpid(),
        "scientific_preflight_image_identifier": scientific_image_identifier,
        "ibm_runtime_image_identifier": ibm_runtime_image_identifier,
        "observed_at_epoch": time.time(),
    }


def _process_request(
    request_path: Path,
    *,
    requests: Path,
    handoffs: Path,
    run_root: Path,
    repository_root: Path,
    scientific_image_identifier: str,
    ibm_runtime_image_identifier: str,
) -> None:
    request = IBMRunBoundPreflightRequest.model_validate(
        _controlled_json(
            requests,
            request_path.name,
            maximum_bytes=_MAXIMUM_BYTES,
        )
    )
    handoff_path = handoffs / request_path.name
    if handoff_path.exists() or handoff_path.is_symlink():
        return
    if (
        request.scientific_preflight_image_identifier
        != scientific_image_identifier
        or request.ibm_runtime_image_identifier != ibm_runtime_image_identifier
    ):
        raise ValueError("IBM preflight request image identity mismatch.")
    run_directory = run_root / request.run_identifier
    if (
        _RUN_IDENTIFIER.fullmatch(request.run_identifier) is None
        or run_directory.is_symlink()
        or not run_directory.is_dir()
        or run_directory.resolve(strict=True).parent != run_root
    ):
        raise ValueError("IBM preflight request run directory is invalid.")
    manifest = ManifestEnvelope.model_validate(request.manifest)
    try:
        output = ExistingQuantumPreflightExecutor(
            repository_root=repository_root,
            image_identifier=scientific_image_identifier,
        ).execute_ibm_preflight(
            manifest,
            preset_identifier=request.preset_identifier,
            run_directory=run_directory,
            maximum_seconds=request.maximum_seconds,
        )
        payload: dict[str, Any] = {
            "output": {
                "results": output.results,
                "verification": output.verification,
                "receipt": output.receipt,
                "runner_summary": output.runner_summary,
            }
        }
    except Exception:
        payload = {"failure_code": "isolated_preflight_failed"}
    write_json_atomic(
        handoff_path,
        {
            "schema_version": IBM_PREFLIGHT_HANDOFF_SCHEMA,
            "run_identifier": request.run_identifier,
            "experiment_sha256": manifest.experiment.fingerprint,
            "network_boundary": "docker_network_none",
            "network_disabled": True,
            "network_namespace_sha256": sha256_fingerprint(
                {
                    "interfaces": ["lo"],
                    "launcher_mode": "run_bound_file_coordinator",
                }
            ),
            "scientific_preflight_image_identifier": scientific_image_identifier,
            "ibm_runtime_image_identifier": ibm_runtime_image_identifier,
            **payload,
        },
        maximum_bytes=_MAXIMUM_BYTES,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exchange-root", required=True, type=Path)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--repository-root", default="/app", type=Path)
    parser.add_argument("--scientific-image-identifier", required=True)
    parser.add_argument("--ibm-runtime-image-identifier", required=True)
    arguments = parser.parse_args()

    _assert_no_ibm_credentials()
    _assert_no_network_boundary()
    if (
        re.fullmatch(
            r"sha256:[0-9a-f]{64}", arguments.scientific_image_identifier
        )
        is None
        or re.fullmatch(
            r"sha256:[0-9a-f]{64}", arguments.ibm_runtime_image_identifier
        )
        is None
        or arguments.scientific_image_identifier
        == arguments.ibm_runtime_image_identifier
    ):
        raise ValueError("Preflight coordinator image identities are invalid.")

    exchange_root = _validated_directory(arguments.exchange_root)
    requests = _validated_directory(exchange_root / "requests", parent=exchange_root)
    handoffs = _validated_directory(exchange_root / "handoffs", parent=exchange_root)
    run_root = _validated_directory(arguments.run_root)
    repository_root = _validated_directory(arguments.repository_root)
    readiness_path = exchange_root / "launcher-readiness.json"

    stop = threading.Event()

    def request_stop(_signum: int, _frame: Any) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    heartbeat_errors: list[Exception] = []

    def heartbeat() -> None:
        while not stop.is_set():
            try:
                write_json_atomic(
                    readiness_path,
                    _readiness(
                        scientific_image_identifier=(
                            arguments.scientific_image_identifier
                        ),
                        ibm_runtime_image_identifier=(
                            arguments.ibm_runtime_image_identifier
                        ),
                    ),
                    maximum_bytes=100_000,
                )
            except Exception as exc:
                heartbeat_errors.append(exc)
                stop.set()
                return
            stop.wait(0.5)

    heartbeat_thread = threading.Thread(
        target=heartbeat,
        name="ibm-preflight-launcher-heartbeat",
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        while not stop.is_set():
            for request_path in sorted(requests.glob("run-*.json")):
                if request_path.is_symlink() or not request_path.is_file():
                    continue
                _process_request(
                    request_path,
                    requests=requests,
                    handoffs=handoffs,
                    run_root=run_root,
                    repository_root=repository_root,
                    scientific_image_identifier=arguments.scientific_image_identifier,
                    ibm_runtime_image_identifier=arguments.ibm_runtime_image_identifier,
                )
            stop.wait(0.1)
    finally:
        stop.set()
        heartbeat_thread.join(timeout=2)
    if heartbeat_thread.is_alive() or heartbeat_errors:
        raise RuntimeError("IBM preflight launcher heartbeat failed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
