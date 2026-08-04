"""Controlled single-node production startup for the Pulsate API."""

from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path
from typing import Any, Callable

from .config_check import validate_configuration
from .production_configuration import EnvironmentConfigurationSource


class ProductionStartupError(RuntimeError):
    """Path-free production startup failure."""


def validate_application_data_layout(data_root: Path) -> None:
    """Validate or create only the application-owned production store roots."""

    required_directories = (
        "experiments",
        "interpretations",
        "molecular-artifacts",
        "molecular-projects",
        "runs",
        "security",
    )
    try:
        root_metadata = data_root.lstat()
        if _unsafe(data_root, root_metadata.st_mode) or not stat.S_ISDIR(
            root_metadata.st_mode
        ):
            raise OSError
        trusted_root = data_root.resolve(strict=True)
        for name in required_directories:
            candidate = data_root / name
            candidate.mkdir(mode=0o700, exist_ok=True)
            metadata = candidate.lstat()
            if _unsafe(candidate, metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise OSError
            resolved = candidate.resolve(strict=True)
            if resolved.parent != trusted_root:
                raise OSError
    except OSError:
        raise ProductionStartupError(
            "Production application data layout is unavailable or unsafe."
        ) from None


def serve_main(
    argv: list[str] | None = None,
    *,
    configuration_source: EnvironmentConfigurationSource | None = None,
    configuration_validator: Callable[..., dict[str, object]] = validate_configuration,
    server_runner: Callable[..., Any] | None = None,
) -> int:
    """Validate all production dependencies before asking Uvicorn to listen."""

    try:
        default_port = int(os.environ.get("PULSATE_SERVER_PORT", "8000"))
        default_workers = int(os.environ.get("PULSATE_SERVER_WORKERS", "1"))
        default_shutdown_timeout = int(
            os.environ.get("PULSATE_SERVER_SHUTDOWN_TIMEOUT_SECONDS", "30")
        )
    except ValueError:
        print(json.dumps({"status": "failed", "category": "server_configuration"}))
        return 2
    parser = argparse.ArgumentParser(prog="cgr-pulsate-serve")
    parser.add_argument("--host", default=os.environ.get("PULSATE_SERVER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=default_port)
    parser.add_argument("--workers", type=int, default=default_workers)
    parser.add_argument(
        "--shutdown-timeout",
        type=int,
        default=default_shutdown_timeout,
    )
    arguments = parser.parse_args(argv)
    if (
        not arguments.host
        or not 1 <= arguments.port <= 65535
        or arguments.workers != 1
        or not 1 <= arguments.shutdown_timeout <= 300
    ):
        print(json.dumps({"status": "failed", "category": "server_configuration"}))
        return 2
    try:
        source = configuration_source or EnvironmentConfigurationSource()
        result = configuration_validator(source)
        values = source.load()
        data_root_value = values.get("PULSATE_APPLICATION_DATA_ROOT")
        if data_root_value is None:
            raise ProductionStartupError("Production configuration is unavailable.")
        validate_application_data_layout(Path(data_root_value))
        runner = server_runner
        if runner is None:
            import uvicorn

            runner = uvicorn.run
        print(
            json.dumps(
                {
                    "status": "validated",
                    "configuration_fingerprint": result["configuration_fingerprint"],
                    "listener": "starting",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        runner(
            "cgr.pulsate_api.app:app",
            host=arguments.host,
            port=arguments.port,
            workers=arguments.workers,
            timeout_graceful_shutdown=arguments.shutdown_timeout,
            access_log=True,
        )
        return 0
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        print(json.dumps({"status": "failed", "category": "startup_validation"}))
        return 1


def _unsafe(path: Path, mode: int) -> bool:
    junction = getattr(path, "is_junction", None)
    return stat.S_ISLNK(mode) or bool(junction and junction())


__all__ = ["ProductionStartupError", "serve_main", "validate_application_data_layout"]


if __name__ == "__main__":
    raise SystemExit(serve_main())
