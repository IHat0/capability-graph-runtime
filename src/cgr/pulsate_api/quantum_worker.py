"""Isolated main-thread quantum execution.

Exit codes are 0 for completion, 4 for scientific rejection, 7 for the
scientific timeout, and 3 for all other controlled worker failures.
"""

from __future__ import annotations

import argparse
import json
import re
import stat
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from cgr.quantum_preflight.artifacts import write_json_atomic
from cgr.quantum_preflight.contracts import ManifestEnvelope
from cgr.quantum_preflight.errors import QuantumTimeoutError, QuantumVerificationError
from cgr.quantum_preflight.runner import run_trusted_reference

WORKER_RESULT_SCHEMA = "cgr.pulsate-quantum-worker-result/1.0.0"
WORKER_RESULT_MAXIMUM_BYTES = 64 * 1024
WORKER_MANIFEST_MAXIMUM_BYTES = 2 * 1024 * 1024

WORKER_EXIT_COMPLETED = 0
WORKER_EXIT_FAILED = 3
WORKER_EXIT_VERIFICATION_FAILED = 4
WORKER_EXIT_TIMED_OUT = 7

WorkerOutcome = Literal["completed", "verification_failed", "timed_out", "failed"]
WORKER_EXIT_CODES: dict[WorkerOutcome, int] = {
    "completed": WORKER_EXIT_COMPLETED,
    "verification_failed": WORKER_EXIT_VERIFICATION_FAILED,
    "timed_out": WORKER_EXIT_TIMED_OUT,
    "failed": WORKER_EXIT_FAILED,
}
_RUN_DIRECTORY_NAME = re.compile(r"^run-[0-9a-f]{32}$")


class WorkerError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error_type: str
    message: str


class WorkerResultEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[WORKER_RESULT_SCHEMA] = WORKER_RESULT_SCHEMA
    outcome: WorkerOutcome
    summary: dict[str, Any] | None = None
    error: WorkerError | None = None

    @model_validator(mode="after")
    def validate_outcome_payload(self) -> "WorkerResultEnvelope":
        if self.outcome == "completed":
            if self.summary is None or self.error is not None:
                raise ValueError("A completed worker result requires only a summary.")
        elif self.summary is not None or self.error is None:
            raise ValueError("A failed worker result requires only structured error details.")
        return self


Runner = Callable[..., dict[str, Any]]


def _bounded_error(exc: Exception) -> WorkerError:
    message = (str(exc).strip() or type(exc).__name__)[:1000]
    return WorkerError(error_type=type(exc).__name__[:128], message=message)


def _controlled_json(path: Path, *, maximum_bytes: int) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError("The controlled worker manifest is missing.") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("The controlled worker manifest must be a regular file.")
    if metadata.st_size > maximum_bytes:
        raise ValueError("The controlled worker manifest exceeds its size limit.")
    data = path.read_bytes()
    if len(data) > maximum_bytes:
        raise ValueError("The controlled worker manifest exceeds its size limit.")
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("The controlled worker manifest must be a JSON object.")
    return value


def _validate_result_envelope_destination(result_envelope_path: Path) -> Path:
    if result_envelope_path.name != "result.json":
        raise ValueError("The quantum worker result envelope has an invalid name.")
    if result_envelope_path.is_symlink() or result_envelope_path.exists():
        raise ValueError("The quantum worker result envelope must not pre-exist.")
    worker_directory = result_envelope_path.parent
    if worker_directory.name != "quantum-worker":
        raise ValueError("The quantum worker control directory has an invalid name.")
    try:
        worker_metadata = worker_directory.lstat()
        run_metadata = worker_directory.parent.lstat()
    except FileNotFoundError as exc:
        raise ValueError("The quantum worker control hierarchy is missing.") from exc
    if stat.S_ISLNK(worker_metadata.st_mode) or not stat.S_ISDIR(
        worker_metadata.st_mode
    ):
        raise ValueError("The quantum worker control directory is invalid.")
    if stat.S_ISLNK(run_metadata.st_mode) or not stat.S_ISDIR(run_metadata.st_mode):
        raise ValueError("The quantum worker run directory is invalid.")
    run_directory = worker_directory.parent
    if not _RUN_DIRECTORY_NAME.fullmatch(run_directory.name):
        raise ValueError("The quantum worker control directory escaped its run directory.")
    resolved_run = run_directory.resolve(strict=True)
    resolved_worker = worker_directory.resolve(strict=True)
    if resolved_worker.parent != resolved_run:
        raise ValueError("The quantum worker control directory escaped its run directory.")
    controlled_destination = resolved_worker / "result.json"
    if controlled_destination != result_envelope_path.resolve(strict=False):
        raise ValueError("The quantum worker result envelope escaped its control directory.")
    return controlled_destination


def _validate_worker_inputs(
    manifest_path: Path,
    result_root: Path,
    lock_path: Path,
    result_envelope_path: Path,
) -> None:
    worker_directory = result_envelope_path.parent
    resolved_worker = worker_directory.resolve(strict=True)
    resolved_run = resolved_worker.parent
    if (
        manifest_path.name != "manifest.json"
        or result_root.name != "runner-artifacts"
    ):
        raise ValueError("The quantum worker received an invalid controlled layout.")
    if manifest_path.parent.resolve(strict=True) != resolved_worker:
        raise ValueError("The quantum worker manifest escaped its control directory.")
    if result_root.parent.resolve(strict=True) != resolved_run:
        raise ValueError("The quantum worker result root escaped its run directory.")
    if result_root.is_symlink() or result_root.exists():
        raise ValueError("The quantum worker result root must not pre-exist.")
    if lock_path.is_symlink() or not lock_path.is_file():
        raise ValueError("The scientific lock must be a regular file.")


def _write_result(path: Path, result: WorkerResultEnvelope) -> None:
    if path.is_symlink() or path.exists():
        raise ValueError("The quantum worker result envelope must be a new regular file.")
    write_json_atomic(
        path,
        result.model_dump(mode="json"),
        maximum_bytes=WORKER_RESULT_MAXIMUM_BYTES,
    )


def run_worker(
    *,
    manifest_path: Path,
    result_root: Path,
    lock_path: Path,
    image_identifier: str,
    maximum_seconds: int,
    result_envelope_path: Path,
    runner: Runner = run_trusted_reference,
) -> int:
    """Run trusted science on this interpreter's main thread and record control status."""
    try:
        controlled_result_path = _validate_result_envelope_destination(
            result_envelope_path
        )
    except Exception:
        return WORKER_EXIT_FAILED

    try:
        _validate_worker_inputs(
            manifest_path, result_root, lock_path, controlled_result_path
        )
        if maximum_seconds <= 0:
            raise ValueError("The worker maximum duration must be positive.")
        manifest = ManifestEnvelope.model_validate(
            _controlled_json(
                manifest_path, maximum_bytes=WORKER_MANIFEST_MAXIMUM_BYTES
            )
        )
        summary = runner(
            manifest,
            result_root=result_root,
            lock_path=lock_path,
            image_identifier=image_identifier,
            maximum_seconds=maximum_seconds,
        )
        if not isinstance(summary, dict):
            raise TypeError("The trusted runner returned an invalid summary.")
        result = WorkerResultEnvelope(outcome="completed", summary=summary)
    except QuantumVerificationError as exc:
        result = WorkerResultEnvelope(
            outcome="verification_failed", error=_bounded_error(exc)
        )
    except QuantumTimeoutError as exc:
        result = WorkerResultEnvelope(outcome="timed_out", error=_bounded_error(exc))
    except Exception as exc:
        result = WorkerResultEnvelope(outcome="failed", error=_bounded_error(exc))

    exit_code = WORKER_EXIT_CODES[result.outcome]
    try:
        _write_result(controlled_result_path, result)
    except Exception as exc:
        fallback = WorkerResultEnvelope(outcome="failed", error=_bounded_error(exc))
        try:
            _write_result(controlled_result_path, fallback)
        except Exception:
            pass
        return WORKER_EXIT_FAILED
    return exit_code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-json", required=True, type=Path)
    parser.add_argument("--result-root", required=True, type=Path)
    parser.add_argument("--scientific-lock", required=True, type=Path)
    parser.add_argument("--image-identifier", required=True)
    parser.add_argument("--maximum-seconds", required=True, type=int)
    parser.add_argument("--result-envelope", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    return run_worker(
        manifest_path=arguments.manifest_json,
        result_root=arguments.result_root,
        lock_path=arguments.scientific_lock,
        image_identifier=arguments.image_identifier,
        maximum_seconds=arguments.maximum_seconds,
        result_envelope_path=arguments.result_envelope,
    )


if __name__ == "__main__":
    raise SystemExit(main())
