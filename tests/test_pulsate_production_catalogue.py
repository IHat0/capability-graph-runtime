"""Production capability-catalogue trust, lifecycle, and planning tests."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cgr.kernel.contracts import CapabilityVersion
from cgr.pulsate_api.app import _load_preset, create_app
from cgr.pulsate_api.config_check import main as configuration_check_main
from cgr.pulsate_api.config_check import validate_configuration
from cgr.pulsate_api.experiments import ExperimentStore
from cgr.pulsate_api.natural_language import NaturalLanguageInterpretationStore
from cgr.pulsate_api.production_catalogue import (
    ProductionCapabilityCatalogue,
    ProductionCatalogueError,
)
from cgr.pulsate_api.production_configuration import (
    MappingConfigurationSource,
    ProductionConfigurationError,
    load_production_configuration,
)
from cgr.pulsate_api.production_security import ProductionSecurityServices
from cgr.pulsate_api.runs import RunCoordinator
from cgr.pulsate_api.security import StaticAuthenticator
from cgr.pulsate_api.security_contracts import AuthenticatedPrincipal, ProtectedAction
from cgr.science import (
    PRODUCTION_CATALOGUE_SCHEMA_VERSION,
    CapabilityDescriptor,
    CapabilityExecutionEnvelope,
    CapabilityPrerequisite,
    CapabilityPrerequisiteType,
    DeterminismClassification,
    ProductionCapabilityCatalogueDocument,
    ScientificCapabilityCatalogError,
    capability_entry_fingerprint,
    catalogue_source_fingerprint,
    canonical_json,
    complete_catalogue_fingerprint,
)


VERSION = CapabilityVersion(major=1, minor=0, patch=0)
SENTINEL_SECRET = "catalogue-secret-that-must-not-escape"


class _NoExecution:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("Catalogue validation cannot execute scientific work.")


def _principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject_identifier="scientist-alpha",
        tenant_identifier="tenant-alpha",
        authentication_method="test-static",
        scopes=tuple(action.value for action in ProtectedAction),
        roles=("scientist",),
        audit_identifier="scientist-alpha",
    )


def _envelope(
    name: str = "science.production-example",
    *,
    version: CapabilityVersion = VERSION,
    conditional: bool = False,
) -> CapabilityExecutionEnvelope:
    prerequisites = (
        CapabilityPrerequisite(
            prerequisite_identifier="prerequisite.declared-tool",
            prerequisite_type=CapabilityPrerequisiteType.TOOL,
            required_identifier="tool.declared",
            blocking=False,
            explanation="The declared production tool must be provisioned.",
        ),
    ) if conditional else ()
    return CapabilityExecutionEnvelope(
        descriptor=CapabilityDescriptor(
            capability_name=name,
            version=version,
            accepted_artifact_types=("molecular_structure",),
            produced_artifact_types=("analysis_result",),
            required_tools=("tool.declared",),
            determinism=DeterminismClassification.DETERMINISTIC,
        ),
        supported_objective_types=("interaction_analysis",),
        prerequisites=prerequisites,
        execution_targets=("target.declared-local",),
        known_limitations=("Availability is declaration- and evidence-bound.",),
    )


def _entry(
    name: str = "science.production-example",
    *,
    version: CapabilityVersion = VERSION,
    availability: str = "available",
) -> dict[str, Any]:
    conditional = availability == "conditional"
    value: dict[str, Any] = {
        "capability_kind": "generic_computation",
        "provider_identity": "provider.production-declared",
        "implementation_identity": "implementation.production-declared",
        "configuration_schema_version": VERSION.model_dump(mode="json"),
        "supported_operations": ["interaction_analysis"],
        "availability": availability,
        "declared_limitations": ["Availability is not execution authorization."],
        "evidence_references": (
            [
                {
                    "artifact_identifier": f"evidence.{name}.{version}",
                    "content_sha256": "a" * 64,
                }
            ]
            if availability in {"available", "conditional"}
            else []
        ),
        "envelope": _envelope(
            name,
            version=version,
            conditional=conditional,
        ).model_dump(mode="json"),
    }
    value["entry_fingerprint"] = capability_entry_fingerprint(value)
    return value


def _document(
    entries: list[dict[str, Any]] | None = None,
    *,
    identifier: str = "catalogue.production",
    version: CapabilityVersion = VERSION,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": PRODUCTION_CATALOGUE_SCHEMA_VERSION,
        "catalogue_identifier": identifier,
        "catalogue_version": version.model_dump(mode="json"),
        "entries": entries if entries is not None else [_entry()],
        "released_at": datetime(2026, 8, 3, tzinfo=UTC).isoformat().replace(
            "+00:00", "Z"
        ),
        "release_metadata": {"release_channel": "test-only"},
    }
    value["source_fingerprint"] = catalogue_source_fingerprint(value)
    value["catalogue_fingerprint"] = complete_catalogue_fingerprint(value)
    return value


def _write_document(path: Path, document: dict[str, Any]) -> None:
    path.write_text(canonical_json(document), encoding="utf-8", newline="")


def _values(
    tmp_path: Path,
    document: dict[str, Any] | None = None,
    *,
    enabled: bool = True,
    required: bool = True,
    allow_empty: bool = False,
    maximum_bytes: int = 1024 * 1024,
    maximum_entries: int = 256,
) -> dict[str, str]:
    data = tmp_path / "data"
    trusted = tmp_path / "trusted"
    data.mkdir(parents=True, exist_ok=True)
    trusted.mkdir(parents=True, exist_ok=True)
    (trusted / "jwks.json").write_text('{"keys":[]}', encoding="utf-8")
    values = {
        "PULSATE_ENVIRONMENT": "production",
        "PULSATE_SERVICE_IDENTITY": "pulsate-api-production",
        "PULSATE_APPLICATION_DATA_ROOT": str(data.resolve()),
        "PULSATE_TRUSTED_CONFIGURATION_ROOT": str(trusted.resolve()),
        "PULSATE_AUTH_ISSUER": "https://identity.example.test/",
        "PULSATE_AUTH_AUDIENCES": "pulsate-api",
        "PULSATE_AUTH_ALGORITHMS": "RS256",
        "PULSATE_AUTH_JWKS_FILE": "jwks.json",
        "PULSATE_GRANT_DATABASE_FILE": "security/grants.sqlite3",
        "PULSATE_AUDIT_DATABASE_FILE": "security/audit.sqlite3",
        "PULSATE_CATALOGUE_ENABLED": str(enabled).lower(),
        "PULSATE_CATALOGUE_REQUIRED": str(required).lower(),
        "PULSATE_CATALOGUE_MAXIMUM_BYTES": str(maximum_bytes),
        "PULSATE_CATALOGUE_MAXIMUM_ENTRIES": str(maximum_entries),
        "PULSATE_CATALOGUE_ALLOW_EMPTY": str(allow_empty).lower(),
    }
    if enabled:
        selected = document or _document()
        catalogue_file = trusted / "catalogue.json"
        _write_document(catalogue_file, selected)
        values.update(
            PULSATE_CATALOGUE_FILE="catalogue.json",
            PULSATE_CATALOGUE_EXPECTED_IDENTIFIER=str(
                selected["catalogue_identifier"]
            ),
            PULSATE_CATALOGUE_EXPECTED_VERSION=".".join(
                str(selected["catalogue_version"][part])
                for part in ("major", "minor", "patch")
            ),
            PULSATE_CATALOGUE_EXPECTED_FINGERPRINT=str(
                selected["catalogue_fingerprint"]
            ),
        )
    return values


def _configuration(tmp_path: Path, document: dict[str, Any] | None = None, **options: Any):
    return load_production_configuration(
        MappingConfigurationSource(_values(tmp_path, document, **options))
    )


def _catalogue(tmp_path: Path, document: dict[str, Any] | None = None, **options: Any):
    configuration = _configuration(tmp_path, document, **options)
    return ProductionCapabilityCatalogue(
        configuration.catalogue,
        trusted_configuration_root=configuration.repositories.trusted_configuration_root,
    )


def _services(configuration) -> ProductionSecurityServices:
    return ProductionSecurityServices(
        configuration,
        authenticator=StaticAuthenticator({"test-token": _principal()}),
    )


def _application(tmp_path: Path, configuration) -> tuple[Any, _NoExecution]:
    executor = _NoExecution()
    app = create_app(
        coordinator=RunCoordinator(
            run_root=tmp_path / "runs",
            manifest_resolver=_load_preset,
            executor=executor,
            enabled=False,
        ),
        experiment_store=ExperimentStore(tmp_path / "experiments"),
        natural_language_store=NaturalLanguageInterpretationStore(
            tmp_path / "interpretations",
            None,
            unavailable_reason="disabled for catalogue tests",
        ),
        security_services=_services(configuration),
    )
    return app, executor


def test_valid_production_catalogue_and_fingerprints_are_deterministic(tmp_path: Path) -> None:
    document = _document()
    first = _catalogue(tmp_path, document)
    first.start()
    loaded = first.document
    assert isinstance(loaded, ProductionCapabilityCatalogueDocument)
    assert loaded.catalogue_fingerprint == document["catalogue_fingerprint"]
    assert loaded.source_fingerprint == document["source_fingerprint"]
    assert loaded.entries[0].entry_fingerprint == document["entries"][0]["entry_fingerprint"]
    assert first.envelopes() == (loaded.entries[0].envelope,)
    second = ProductionCapabilityCatalogueDocument.model_validate(document)
    assert second.to_document_json() == loaded.to_document_json()
    assert second.catalogue_fingerprint == loaded.catalogue_fingerprint


@pytest.mark.parametrize(
    "mutation",
    (
        "duplicate",
        "ordering",
        "schema",
        "missing",
        "kind",
        "availability_evidence",
        "entry_fingerprint",
        "catalogue_fingerprint",
    ),
)
def test_malformed_or_incompatible_catalogues_fail_closed(tmp_path: Path, mutation: str) -> None:
    document = _document()
    if mutation == "duplicate":
        document = _document([_entry(), _entry()])
    elif mutation == "ordering":
        document = _document([_entry("science.beta"), _entry("science.alpha")])
    elif mutation == "schema":
        document["schema_version"] = "unsupported/9.0.0"
    elif mutation == "missing":
        document["entries"][0].pop("provider_identity")
    elif mutation == "kind":
        document["entries"][0]["capability_kind"] = "fabricated_engine"
    elif mutation == "availability_evidence":
        document["entries"][0]["evidence_references"] = []
        document["entries"][0]["entry_fingerprint"] = capability_entry_fingerprint(
            document["entries"][0]
        )
    elif mutation == "entry_fingerprint":
        document["entries"][0]["entry_fingerprint"] = "f" * 64
    else:
        document["catalogue_fingerprint"] = "f" * 64
    if mutation not in {"duplicate", "catalogue_fingerprint"}:
        document["source_fingerprint"] = catalogue_source_fingerprint(document)
        document["catalogue_fingerprint"] = complete_catalogue_fingerprint(document)
    catalogue = _catalogue(tmp_path, document)
    with pytest.raises(ProductionCatalogueError) as error:
        catalogue.start()
    assert str(tmp_path) not in str(error.value)
    assert SENTINEL_SECRET not in str(error.value)


def test_catalogue_size_and_entry_count_limits_fail_closed(tmp_path: Path) -> None:
    oversized = _catalogue(tmp_path / "oversized", maximum_bytes=1024)
    with pytest.raises(ProductionCatalogueError):
        oversized.start()
    entries = [
        _entry("science.alpha"),
        _entry("science.beta"),
    ]
    limited = _catalogue(
        tmp_path / "count",
        _document(entries),
        maximum_entries=1,
    )
    with pytest.raises(ProductionCatalogueError):
        limited.start()


def test_catalogue_configuration_rejects_traversal_and_expectation_mismatches(tmp_path: Path) -> None:
    values = _values(tmp_path / "traversal")
    values["PULSATE_CATALOGUE_FILE"] = "../catalogue.json"
    with pytest.raises(ProductionConfigurationError):
        load_production_configuration(MappingConfigurationSource(values))

    for field, value in (
        ("PULSATE_CATALOGUE_EXPECTED_IDENTIFIER", "catalogue.other"),
        ("PULSATE_CATALOGUE_EXPECTED_VERSION", "2.0.0"),
        ("PULSATE_CATALOGUE_EXPECTED_FINGERPRINT", "f" * 64),
    ):
        mismatch_values = _values(tmp_path / field)
        mismatch_values[field] = value
        configuration = load_production_configuration(
            MappingConfigurationSource(mismatch_values)
        )
        catalogue = ProductionCapabilityCatalogue(
            configuration.catalogue,
            trusted_configuration_root=(
                configuration.repositories.trusted_configuration_root
            ),
        )
        with pytest.raises(ProductionCatalogueError):
            catalogue.start()


def test_symlinked_catalogue_source_is_rejected_where_supported(tmp_path: Path) -> None:
    values = _values(tmp_path)
    trusted = Path(values["PULSATE_TRUSTED_CONFIGURATION_ROOT"])
    source = trusted / "catalogue.json"
    outside = tmp_path / "outside.json"
    outside.write_bytes(source.read_bytes())
    source.unlink()
    try:
        source.symlink_to(outside)
    except OSError:
        pytest.skip("This platform cannot create a catalogue symlink.")
    configuration = load_production_configuration(MappingConfigurationSource(values))
    catalogue = ProductionCapabilityCatalogue(
        configuration.catalogue,
        trusted_configuration_root=trusted,
    )
    with pytest.raises(ProductionCatalogueError) as error:
        catalogue.start()
    assert str(tmp_path) not in str(error.value)


def test_explicit_empty_catalogue_requires_permission(tmp_path: Path) -> None:
    allowed = _catalogue(
        tmp_path / "allowed",
        _document([]),
        allow_empty=True,
    )
    allowed.start()
    assert allowed.ready()
    assert allowed.envelopes() == ()

    denied = _catalogue(tmp_path / "denied", _document([]), allow_empty=False)
    with pytest.raises(ProductionCatalogueError):
        denied.start()

    disabled = _catalogue(
        tmp_path / "disabled",
        enabled=False,
        required=False,
        allow_empty=True,
    )
    disabled.start()
    assert disabled.envelopes() == ()


def test_unknown_and_unavailable_capabilities_are_not_planner_candidates(tmp_path: Path) -> None:
    document = _document(
        [
            _entry("science.available", availability="available"),
            _entry("science.conditional", availability="conditional"),
            _entry("science.unavailable", availability="unavailable"),
            _entry("science.unknown", availability="unknown"),
        ]
    )
    catalogue = _catalogue(tmp_path, document)
    catalogue.start()
    assert tuple(item.descriptor.capability_name for item in catalogue.envelopes()) == (
        "science.available",
        "science.conditional",
    )
    assert catalogue.get("science.conditional", VERSION).prerequisites
    with pytest.raises(ScientificCapabilityCatalogError):
        catalogue.get("science.unknown", VERSION)


def test_catalogue_is_not_reread_per_request_and_concurrency_observes_one_snapshot(tmp_path: Path) -> None:
    configuration = _configuration(tmp_path)
    catalogue = ProductionCapabilityCatalogue(
        configuration.catalogue,
        trusted_configuration_root=configuration.repositories.trusted_configuration_root,
    )
    catalogue.start()
    expected = catalogue.envelopes()
    configuration.catalogue.catalogue_file.write_text("malformed", encoding="utf-8")
    with ThreadPoolExecutor(max_workers=8) as pool:
        snapshots = list(pool.map(lambda _: catalogue.envelopes(), range(64)))
    assert all(snapshot == expected for snapshot in snapshots)


def test_failed_reload_preserves_previous_valid_snapshot(tmp_path: Path) -> None:
    configuration = _configuration(tmp_path)
    catalogue = ProductionCapabilityCatalogue(
        configuration.catalogue,
        trusted_configuration_root=configuration.repositories.trusted_configuration_root,
    )
    catalogue.start()
    previous = catalogue.envelopes()
    configuration.catalogue.catalogue_file.write_text("{}", encoding="utf-8")
    with pytest.raises(ProductionCatalogueError):
        catalogue.reload()
    assert catalogue.envelopes() == previous


@pytest.mark.parametrize("failure", ("missing", "malformed", "fingerprint"))
def test_invalid_mandatory_catalogue_prevents_readiness_without_execution(
    tmp_path: Path, failure: str
) -> None:
    values = _values(tmp_path)
    path = Path(values["PULSATE_TRUSTED_CONFIGURATION_ROOT"], "catalogue.json")
    if failure == "missing":
        path.unlink()
    elif failure == "malformed":
        path.write_text("not-json", encoding="utf-8")
    else:
        document = json.loads(path.read_text(encoding="utf-8"))
        document["catalogue_fingerprint"] = "f" * 64
        _write_document(path, document)
    configuration = load_production_configuration(MappingConfigurationSource(values))
    app, executor = _application(tmp_path, configuration)
    with TestClient(app) as client:
        assert client.get("/live").status_code == 200
        ready = client.get("/ready")
        assert not app.state.security_services.ready()
    assert ready.status_code == 503
    assert ready.json()["detail"]["code"] == "service_not_ready"
    assert str(tmp_path) not in ready.text
    assert executor.calls == 0


def test_valid_catalogue_is_injected_into_planning_and_readiness(tmp_path: Path) -> None:
    configuration = _configuration(tmp_path)
    app, executor = _application(tmp_path, configuration)
    with TestClient(app) as client:
        ready = client.get("/ready")
        service = app.state.molecular_planning_service
        first = service.catalogue.envelopes()
        second = service.catalogue.envelopes()
    assert ready.status_code == 200
    assert ready.json()["components"]["catalogue"] == "ready"
    assert first == second
    assert first[0].descriptor.capability_name == "science.production-example"
    assert executor.calls == 0


def test_production_never_falls_back_when_empty_catalogue_is_explicit(
    tmp_path: Path,
) -> None:
    configuration = _configuration(
        tmp_path,
        enabled=False,
        required=False,
        allow_empty=True,
    )
    app, executor = _application(tmp_path, configuration)
    with TestClient(app) as client:
        ready = client.get("/ready")
        configured = app.state.molecular_planning_service.catalogue.envelopes()
    assert ready.status_code == 200
    assert configured == ()
    assert executor.calls == 0


def test_configuration_check_validates_catalogue_and_reports_only_safe_identity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    values = _values(tmp_path)

    def factory(configuration):
        return _services(configuration)

    source = MappingConfigurationSource(values)
    result = validate_configuration(source, services_factory=factory)
    assert result["catalogue"] == {
        "status": "ready",
        "identifier": "catalogue.production",
        "version": "1.0.0",
        "fingerprint": values["PULSATE_CATALOGUE_EXPECTED_FINGERPRINT"],
        "entry_count": 1,
    }
    assert configuration_check_main(
        [],
        source=source,
        services_factory=factory,
    ) == 0
    output = capsys.readouterr().out
    assert "catalogue.production" in output
    assert str(tmp_path) not in output
    assert SENTINEL_SECRET not in output


def test_configuration_check_rejects_invalid_catalogue_path_free(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    values = _values(tmp_path)
    Path(values["PULSATE_TRUSTED_CONFIGURATION_ROOT"], "catalogue.json").write_text(
        "{}", encoding="utf-8"
    )
    assert configuration_check_main(
        [],
        source=MappingConfigurationSource(values),
        services_factory=_services,
    ) == 1
    output = capsys.readouterr().err
    assert "configuration_validation_failed" in output
    assert str(tmp_path) not in output


def test_configuration_safe_json_excludes_private_catalogue_path(tmp_path: Path) -> None:
    configuration = _configuration(tmp_path)
    safe = configuration.canonical_safe_json()
    assert str(tmp_path) not in safe
    assert configuration.catalogue.expected_identifier in safe
    assert configuration.catalogue.expected_fingerprint in safe
