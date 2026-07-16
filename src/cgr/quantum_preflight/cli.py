"""CLI for the trusted reference side only; no IBM or model-generation options."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .errors import QuantumPreflightError
from .manifests import load_manifest
from .runner import run_trusted_reference


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the trusted LiH quantum preflight reference.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--result-root", type=Path, required=True)
    parser.add_argument("--lock-file", type=Path, default=Path("requirements/quantum-preflight.lock"))
    parser.add_argument("--image-identifier", default=os.environ.get("CGR_QUANTUM_IMAGE_ID", "unrecorded"))
    parser.add_argument("--max-seconds", type=int)
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        summary = run_trusted_reference(
            manifest,
            result_root=args.result_root,
            lock_path=args.lock_file,
            image_identifier=args.image_identifier,
            maximum_seconds=args.max_seconds,
        )
    except QuantumPreflightError as exc:
        print(json.dumps({"authorized": False, "error": str(exc), "exit_code": exc.exit_code}), file=sys.stderr)
        return exc.exit_code
    except Exception as exc:
        print(json.dumps({"authorized": False, "error": str(exc), "exit_code": 3}), file=sys.stderr)
        return 3
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["authorized"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
