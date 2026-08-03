"""Enterprise E2 authentication, authorization, audit, and operations tests."""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from cgr.pulsate_api.app import _load_preset, create_app
from cgr.pulsate_api.experiments import ExperimentStore
from cgr.pulsate_api.natural_language import NaturalLanguageInterpretationStore
from cgr.pulsate_api.molecular_scenes import NativeMolecularSceneUnavailableError
from cgr.pulsate_api.runs import RunCoordinator
from cgr.pulsate_api.security import (
    PUBLIC_ROUTES,
    ROUTE_PROTECTIONS,
    InMemoryAuditSink,
    InMemoryGrantProvider,
    MetricsCollector,
    RouteProtection,
    SecurityServices,
    StaticAuthenticator,
    StructuredEventLogger,
    UnavailableAuthenticator,
    UnavailableAuditSink,
)
from cgr.pulsate_api.security_contracts import (
    AuthenticatedPrincipal,
    ProtectedAction,
    RequestSecurityContext,
    ResourceGrant,
    utc_now,
)


TOKEN = "test-enterprise-token"
ALL_ACTIONS = tuple(sorted(ProtectedAction, key=str))


class _NoExecution:
    def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("Enterprise security tests cannot execute runs.")


class _SceneService:
    def __init__(self) -> None:
        self.calls = 0
        self.started = False

    def start(self) -> None:
        self.started = True

    def close(self) -> None:
        self.started = False

    def describe_scene(self, **_kwargs: str) -> dict[str, object]:
        self.calls += 1
        return {"structures": []}


def _principal(
    *, tenant: str = "tenant-alpha", scopes: tuple[str, ...] | None = None
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject_identifier="scientist-alpha",
        tenant_identifier=tenant,
        authentication_method="test-bearer",
        scopes=scopes or tuple(action.value for action in ALL_ACTIONS),
        roles=("scientist",),
        audit_identifier="scientist-alpha",
    )


def _grant(
    *,
    tenant: str = "tenant-alpha",
    resource_type: str,
    resource_identifier: str = "*",
    actions: tuple[ProtectedAction, ...] = ALL_ACTIONS,
) -> ResourceGrant:
    return ResourceGrant(
        tenant_identifier=tenant,
        subject_identifier="scientist-alpha",
        resource_type=resource_type,
        resource_identifier=resource_identifier,
        allowed_actions=tuple(sorted(actions, key=str)),
    )


def _security(
    *,
    principal: AuthenticatedPrincipal | None = None,
    grants: tuple[ResourceGrant, ...] | None = None,
    audit: InMemoryAuditSink | None = None,
    startup_error: bool = False,
    read_audit_fail_closed: bool = False,
) -> SecurityServices:
    active_principal = principal or _principal()
    return SecurityServices(
        authenticator=StaticAuthenticator(
            {TOKEN: active_principal}, startup_error=startup_error
        ),
        grant_provider=InMemoryGrantProvider(
            grants
            or (
                _grant(resource_type="project"),
                _grant(resource_type="experiment"),
                _grant(resource_type="run"),
            ),
            allow_wildcards=True,
        ),
        audit_sink=audit or InMemoryAuditSink(),
        metrics=MetricsCollector(),
        read_audit_fail_closed=read_audit_fail_closed,
    )


def _application(
    tmp_path: Path,
    security: SecurityServices,
    *,
    scene_service: _SceneService | None = None,
):
    return create_app(
        coordinator=RunCoordinator(
            run_root=tmp_path / "runs",
            manifest_resolver=_load_preset,
            executor=_NoExecution(),
            enabled=False,
        ),
        experiment_store=ExperimentStore(tmp_path / "experiments"),
        natural_language_store=NaturalLanguageInterpretationStore(
            tmp_path / "interpretations",
            None,
            unavailable_reason="disabled for enterprise security tests",
        ),
        molecular_scene_service=scene_service,
        security_services=security,
    )


def _without_default_authentication(client: TestClient) -> None:
    client.headers.pop("Authorization", None)


def test_security_contracts_are_immutable_canonical_and_secret_free() -> None:
    principal = AuthenticatedPrincipal(
        subject_identifier="scientist-alpha",
        tenant_identifier="tenant-alpha",
        authentication_method="test-bearer",
        scopes=("scene.read", "project.read", "scene.read"),
        roles=("viewer", "scientist"),
        audit_identifier="audit-alpha",
    )
    context = RequestSecurityContext(
        principal=principal,
        correlation_identifier="correlation-alpha",
        request_identifier="request-alpha",
        authenticated_at=utc_now(),
    )

    assert principal.scopes == ("project.read", "scene.read")
    assert principal.roles == ("scientist", "viewer")
    assert context.canonical_json() == context.canonical_json()
    assert len(context.fingerprint) == 64
    assert "bearer-token" not in context.canonical_json()
    with pytest.raises(ValidationError):
        principal.subject_identifier = "mutated"  # type: ignore[misc]


def test_missing_malformed_and_invalid_bearer_credentials_fail_closed(
    tmp_path: Path,
) -> None:
    with TestClient(_application(tmp_path, _security())) as client:
        _without_default_authentication(client)
        missing = client.get("/api/v1/experiments/presets")
        malformed = client.get(
            "/api/v1/experiments/presets",
            headers={"Authorization": "Basic unsafe"},
        )
        rejected_secret = "credential-secret-987654"
        invalid = client.get(
            "/api/v1/experiments/presets",
            headers={"Authorization": f"Bearer {rejected_secret}"},
        )

    for response in (missing, malformed, invalid):
        assert response.status_code == 401
        assert response.json()["detail"]["message"] == "Authentication is required."
        assert "unsafe" not in response.text
        assert rejected_secret not in response.text


def test_authenticated_principal_can_read_an_explicitly_granted_project(
    tmp_path: Path,
) -> None:
    scene = _SceneService()
    security = _security(
        grants=(
            _grant(
                resource_type="project",
                resource_identifier="project-alpha",
                actions=(ProtectedAction.PROJECT_READ, ProtectedAction.SCENE_READ),
            ),
        )
    )
    with TestClient(_application(tmp_path, security, scene_service=scene)) as client:
        response = client.get(
            "/api/v1/molecular/scenes/projected",
            params={
                "project_identifier": "project-alpha",
                "scene_identifier": "scene-alpha",
            },
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

    assert response.status_code == 200
    assert scene.calls == 1


def test_cross_tenant_and_missing_project_denials_are_enumeration_safe(
    tmp_path: Path,
) -> None:
    scene = _SceneService()
    security = _security(
        grants=(
            _grant(
                tenant="tenant-other",
                resource_type="project",
                resource_identifier="project-existing",
                actions=(ProtectedAction.PROJECT_READ, ProtectedAction.SCENE_READ),
            ),
        )
    )
    with TestClient(_application(tmp_path, security, scene_service=scene)) as client:
        existing = client.get(
            "/api/v1/molecular/scenes/projected",
            params={"project_identifier": "project-existing", "scene_identifier": "scene"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        missing = client.get(
            "/api/v1/molecular/scenes/projected",
            params={"project_identifier": "project-missing", "scene_identifier": "scene"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

    assert existing.status_code == missing.status_code == 403
    assert existing.json() == missing.json()
    assert scene.calls == 0


def test_artifact_and_planning_access_require_distinct_permissions(
    tmp_path: Path,
) -> None:
    scene = _SceneService()
    security = _security(
        grants=(
            _grant(
                resource_type="project",
                resource_identifier="project-alpha",
                actions=(ProtectedAction.PROJECT_READ,),
            ),
        )
    )
    with TestClient(_application(tmp_path, security, scene_service=scene)) as client:
        artifact = client.get(
            "/api/v1/molecular/scenes/native-structure",
            params={"project_identifier": "project-alpha", "scene_identifier": "scene", "structure_identifier": "structure"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        planning = client.post(
            "/api/v1/molecular/projects/project-alpha/planning/evaluate",
            json={},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

    assert artifact.status_code == planning.status_code == 403
    assert scene.calls == 0


def test_read_scope_neither_grants_execution_nor_approval(tmp_path: Path) -> None:
    principal = _principal(scopes=(ProtectedAction.EXPERIMENT_READ.value,))
    security = _security(
        principal=principal,
        grants=(
            _grant(resource_type="run"),
            _grant(resource_type="experiment"),
        ),
    )
    with TestClient(_application(tmp_path, security)) as client:
        create = client.post(
            "/api/v1/runs",
            json={"preset_identifier": "x", "execution_target": "local_simulator"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        approve = client.post(
            "/api/v1/experiments/interpretation-alpha/approve",
            json={},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

    assert create.status_code == approve.status_code == 403


def test_every_current_application_route_has_an_explicit_security_classification(
    tmp_path: Path,
) -> None:
    application = _application(tmp_path, _security())
    routes = {
        (method, route.path)
        for route in application.routes
        for method in getattr(route, "methods", set())
        if route.path.startswith("/api/") or route.path in {"/live", "/ready"}
    }
    assert routes == set(PUBLIC_ROUTES) | set(ROUTE_PROTECTIONS)


def test_production_without_an_authenticator_is_not_ready_and_fails_closed(
    tmp_path: Path,
) -> None:
    security = SecurityServices(
        authenticator=UnavailableAuthenticator(),
        grant_provider=InMemoryGrantProvider(),
        audit_sink=UnavailableAuditSink(),
    )
    with TestClient(_application(tmp_path, security)) as client:
        readiness = client.get("/ready")
        protected = client.get(
            "/api/v1/experiments/presets",
            headers={"Authorization": "Bearer opaque"},
        )

    assert readiness.status_code == 503
    assert protected.status_code == 503
    assert protected.json()["detail"]["code"] == "authentication_unavailable"


def test_development_authentication_is_explicit_and_forbidden_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PULSATE_ENVIRONMENT", "production")
    monkeypatch.setenv("PULSATE_DEVELOPMENT_AUTH_ENABLED", "true")
    with pytest.raises(ValueError, match="forbidden"):
        SecurityServices.from_environment()
    monkeypatch.setenv("PULSATE_DEVELOPMENT_AUTH_ENABLED", "false")
    with pytest.raises(ValueError, match="PULSATE_APPLICATION_DATA_ROOT"):
        SecurityServices.from_environment()


def test_correlation_identifiers_are_propagated_or_safely_replaced(
    tmp_path: Path,
) -> None:
    with TestClient(_application(tmp_path, _security())) as client:
        supplied = client.get("/api/v1/health", headers={"X-Correlation-ID": "correlation-client-1"})
        invalid = client.get("/api/v1/health", headers={"X-Correlation-ID": "x" * 500})

    assert supplied.headers["X-Correlation-ID"] == "correlation-client-1"
    assert invalid.headers["X-Correlation-ID"].startswith("correlation-")
    assert len(invalid.headers["X-Correlation-ID"]) <= 128


def test_security_logs_audits_and_metrics_are_safe(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    audit = InMemoryAuditSink()
    security = _security(audit=audit)
    secret = "credential-that-must-not-escape"
    caplog.set_level(logging.INFO, logger="cgr.pulsate.security")
    with TestClient(_application(tmp_path, security)) as client:
        denied = client.get(
            "/api/v1/experiments/presets",
            headers={"Authorization": f"Bearer {secret}"},
        )

    assert denied.status_code == 401
    assert any(record.action == "authentication" for record in audit.records)
    logs = "\n".join(record.message for record in caplog.records)
    assert secret not in logs
    for record in caplog.records:
        if record.name == "cgr.pulsate.security":
            json.loads(record.message)
    snapshot = security.metrics.snapshot()
    assert any("authentication_failures" in key for key in snapshot["counters"])
    assert secret not in json.dumps(snapshot)


def test_structured_logger_redacts_sensitive_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "token-value-that-must-not-escape"
    caplog.set_level(logging.INFO, logger="cgr.pulsate.security")
    StructuredEventLogger().emit(
        "security.redaction_test",
        bearer_token=secret,
        molecular_payload=secret,
    )
    emitted = caplog.records[-1].message
    assert secret not in emitted
    assert json.loads(emitted)["bearer_token"] == "redacted"


def test_successful_planning_audit_records_the_exact_result_fingerprint() -> None:
    audit = InMemoryAuditSink()
    security = _security(audit=audit)
    security.start()
    try:
        context = security.authenticate(
            f"Bearer {TOKEN}", "correlation-planning", "request-planning"
        )
        protection = RouteProtection(
            (ProtectedAction.PROJECT_READ, ProtectedAction.PLANNING_EVALUATE),
            "project",
            "static:project-alpha",
            True,
        )
        security.authorize(context, protection, "project-alpha")
        security.audit_planning_result("a" * 64)
    finally:
        security.clear_context()
        security.close()

    completed = [
        record
        for record in audit.records
        if record.reason_category == "planning_completed"
    ]
    assert len(completed) == 1
    assert completed[0].result_fingerprint == "a" * 64


def test_grant_provider_failure_is_controlled_before_repository_access(
    tmp_path: Path,
) -> None:
    scene = _SceneService()
    security = SecurityServices(
        authenticator=StaticAuthenticator({TOKEN: _principal()}),
        grant_provider=InMemoryGrantProvider(failure=True),
        audit_sink=InMemoryAuditSink(),
    )
    with TestClient(_application(tmp_path, security, scene_service=scene)) as client:
        response = client.get(
            "/api/v1/molecular/scenes/projected",
            params={"project_identifier": "project-alpha", "scene_identifier": "scene"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "authorization_unavailable"
    assert scene.calls == 0


def test_repository_failure_response_is_path_free(tmp_path: Path) -> None:
    class _UnavailableScene(_SceneService):
        def describe_scene(self, **_kwargs: str) -> dict[str, object]:
            self.calls += 1
            raise NativeMolecularSceneUnavailableError(
                r"private repository C:\private\tenant\structure.json"
            )

    scene = _UnavailableScene()
    security = _security(
        grants=(
            _grant(
                resource_type="project",
                actions=(ProtectedAction.PROJECT_READ, ProtectedAction.SCENE_READ),
            ),
        )
    )
    with TestClient(_application(tmp_path, security, scene_service=scene)) as client:
        response = client.get(
            "/api/v1/molecular/scenes/projected",
            params={"project_identifier": "project-alpha", "scene_identifier": "scene"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

    assert response.status_code == 503
    assert "C:\\private" not in response.text
    assert "structure.json" not in response.text
    assert any(
        "repository_failures" in key
        for key in security.metrics.snapshot()["counters"]
    )


def test_shutdown_closes_security_services(tmp_path: Path) -> None:
    security = _security()
    with TestClient(_application(tmp_path, security)) as client:
        assert client.get("/ready").status_code == 200
        assert security.ready()
    assert not security.ready()


def test_mandatory_write_audit_failure_prevents_execution_request(
    tmp_path: Path,
) -> None:
    security = SecurityServices(
        authenticator=StaticAuthenticator({TOKEN: _principal()}),
        grant_provider=InMemoryGrantProvider(
            (_grant(resource_type="run"),), allow_wildcards=True
        ),
        audit_sink=UnavailableAuditSink(),
    )
    with TestClient(_application(tmp_path, security)) as client:
        response = client.post(
            "/api/v1/runs",
            json={"preset_identifier": "does-not-matter", "execution_target": "local_simulator"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "audit_unavailable"


def test_authenticator_startup_failure_prevents_readiness(tmp_path: Path) -> None:
    with TestClient(_application(tmp_path, _security(startup_error=True))) as client:
        assert client.get("/ready").status_code == 503
        response = client.get(
            "/api/v1/experiments/presets",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
    assert response.status_code == 503


def test_concurrent_requests_keep_correlation_and_principal_context_isolated(
    tmp_path: Path,
) -> None:
    application = _application(tmp_path, _security())
    with TestClient(application) as client:
        def request(index: int) -> tuple[str, int]:
            correlation = f"correlation-{index}"
            response = client.get(
                "/api/v1/experiments/presets",
                headers={
                    "Authorization": f"Bearer {TOKEN}",
                    "X-Correlation-ID": correlation,
                },
            )
            return response.headers["X-Correlation-ID"], response.status_code

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(request, range(8)))

    assert results == [(f"correlation-{index}", 200) for index in range(8)]


def test_openapi_declares_bearer_security_only_for_protected_routes(
    tmp_path: Path,
) -> None:
    schema = _application(tmp_path, _security()).openapi()
    assert schema["components"]["securitySchemes"]["BearerAuth"]["scheme"] == "bearer"
    assert schema["paths"]["/api/v1/health"]["get"]["security"] == []
    assert schema["paths"]["/api/v1/runs/{run_identifier}"]["get"]["security"] == [
        {"BearerAuth": []}
    ]
