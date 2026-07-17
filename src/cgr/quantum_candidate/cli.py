"""CLI for the trusted host-side quantum-candidate benchmark controller."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .benchmark import run_benchmark


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the hostile LiH candidate benchmark."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--trusted-reference", type=Path, required=True)
    parser.add_argument("--result-root", type=Path, required=True)
    parser.add_argument(
        "--fixture-root",
        type=Path,
        default=Path("benchmark-fixtures/quantum-candidate-v1"),
    )
    parser.add_argument("--candidate-image", required=True)
    parser.add_argument(
        "--candidate-lock",
        type=Path,
        default=Path("requirements/quantum-preflight.lock"),
    )
    args = parser.parse_args(argv)
    try:
        summary = run_benchmark(
            benchmark_manifest_path=args.manifest,
            trusted_reference_directory=args.trusted_reference,
            result_root=args.result_root,
            fixture_root=args.fixture_root,
            candidate_image_identifier=args.candidate_image,
            candidate_lock_path=args.candidate_lock,
        )
    except Exception as exc:
        print(
            json.dumps(
                {"benchmark_passed": False, "error": str(exc), "exit_code": 3},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 3
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["benchmark_passed"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
