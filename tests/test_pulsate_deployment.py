"""Single-node container and pre-listener startup policy tests."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from cgr.pulsate_api.deployment import serve_main, validate_application_data_layout


class _Source:
    def __init__(self, data_root: Path) -> None:
        self._data_root = data_root

    def load(self) -> dict[str, str]:
        return {"PULSATE_APPLICATION_DATA_ROOT": str(self._data_root)}


def test_production_startup_validates_before_listener(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    events: list[str] = []

    def validate(_source: object) -> dict[str, object]:
        events.append("validate")
        return {"configuration_fingerprint": "a" * 64}

    def listen(*_args: object, **_kwargs: object) -> None:
        events.append("listen")

    assert serve_main(
        ["--host", "127.0.0.1", "--port", "8080", "--workers", "1"],
        configuration_source=_Source(data),  # type: ignore[arg-type]
        configuration_validator=validate,
        server_runner=listen,
    ) == 0
    assert events == ["validate", "listen"]
    assert (data / "molecular-artifacts").is_dir()
    assert (data / "scientific-artifacts").is_dir()
    assert (data / "scientific-executions").is_dir()
    assert (data / "research-sessions").is_dir()
    assert (data / "scientific-private-state").is_dir()
    assert (data / "scientific-workflows").is_dir()
    assert (data / "workflows").is_dir()


def test_invalid_configuration_never_opens_listener(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    calls = 0

    def invalid(_source: object) -> dict[str, object]:
        raise ValueError("private details")

    def listen(*_args: object, **_kwargs: object) -> None:
        nonlocal calls
        calls += 1

    assert serve_main(
        [],
        configuration_source=_Source(data),  # type: ignore[arg-type]
        configuration_validator=invalid,
        server_runner=listen,
    ) == 1
    assert calls == 0


def test_invalid_server_environment_fails_safely_before_listener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("PULSATE_SERVER_PORT", "not-an-integer")
    called = False

    def listen(*_args: object, **_kwargs: object) -> None:
        nonlocal called
        called = True

    assert serve_main(
        [],
        configuration_source=_Source(data),  # type: ignore[arg-type]
        server_runner=listen,
    ) == 2
    assert not called


def test_server_failure_is_nonzero_and_path_free(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = tmp_path / "data"
    data.mkdir()

    def validate(_source: object) -> dict[str, object]:
        return {"configuration_fingerprint": "a" * 64}

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(str(tmp_path / "private"))

    assert serve_main(
        [],
        configuration_source=_Source(data),  # type: ignore[arg-type]
        configuration_validator=validate,
        server_runner=fail,
    ) == 1
    output = capsys.readouterr().out
    assert str(tmp_path) not in output
    assert '"category": "startup_validation"' in output


def test_application_data_layout_rejects_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(real, target_is_directory=True)
    except OSError:
        return
    from cgr.pulsate_api.deployment import ProductionStartupError

    try:
        validate_application_data_layout(linked)
    except ProductionStartupError:
        pass
    else:
        raise AssertionError("Symlinked production data root was accepted.")


def test_container_and_compose_are_hardened() -> None:
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    compose = (root / "compose.production.yml").read_text(encoding="utf-8")
    ignored = (root / ".dockerignore").read_text(encoding="utf-8")
    runtime_lock = (
        root / "requirements/pulsate-production-quantum.lock"
    ).read_text(encoding="utf-8")
    assert "USER 10001:10001" in dockerfile
    assert 'ENTRYPOINT ["python", "-m", "cgr.pulsate_api.deployment"]' in dockerfile
    assert "requirements/pulsate-production-quantum.lock" in dockerfile
    assert "python -m pip check" in dockerfile
    assert not any(
        package in runtime_lock
        for package in ("pytest==", "pytest-asyncio==", "iniconfig==")
    )
    assert "read_only: true" in compose
    assert "no-new-privileges:true" in compose
    assert "cap_drop:" in compose and "- ALL" in compose
    assert "/var/run/docker.sock" not in compose
    assert "privileged:" not in compose
    assert "/live" in compose and "/ready" in compose
    assert ".git" in ignored and ".pytest*" in ignored and "*.sqlite3" in ignored
    assert "deployment" in ignored and ".env" in ignored


def test_supply_chain_workflow_is_fail_closed_and_pinned() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/supply-chain-security.yml").read_text(
        encoding="utf-8"
    )
    enterprise = (root / ".github/workflows/enterprise-readiness.yml").read_text(
        encoding="utf-8"
    )
    assert "permissions:\n  contents: read" in workflow
    assert "continue-on-error" not in workflow
    for version in (
        "pip-audit==2.9.0",
        "bandit==1.8.6",
        "gitleaks/gitleaks:v8.24.2",
        "anchore/syft:v1.18.1",
        "aquasec/trivy:0.58.2",
    ):
        assert version in workflow
    assert "continue-on-error" not in enterprise
    assert "cgr-pulsate-enterprise-gate" in enterprise


def test_direct_production_dependencies_are_exact_and_lock_consistent() -> None:
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    assert dependencies
    assert all("==" in item and "@" not in item for item in dependencies)
    assert project["build-system"]["requires"] == ["setuptools==83.0.0"]

    package = json.loads((root / "frontend/package.json").read_text(encoding="utf-8"))
    lock = json.loads(
        (root / "frontend/package-lock.json").read_text(encoding="utf-8")
    )
    production = package["dependencies"]
    assert production == lock["packages"][""]["dependencies"]
    assert all(
        value[0].isdigit() and not any(marker in value for marker in "^~*@")
        for value in production.values()
    )
