"""Enterprise E3A production configuration and durable security tests."""

from __future__ import annotations

import base64
import json
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cgr.pulsate_api.app import _load_preset, create_app
from cgr.pulsate_api.config_check import main as configuration_check_main
from cgr.pulsate_api.config_check import validate_configuration
from cgr.pulsate_api.production_configuration import (
    CONFIGURATION_INPUT_MAXIMUM_BYTES,
    MappingConfigurationSource,
    ProductionConfigurationError,
    SecretValue,
    load_production_configuration,
)
from cgr.pulsate_api.production_security import (
    AUDIT_GENESIS_FINGERPRINT,
    OfflineJWKSAuthenticator,
    ProductionSecurityProviderError,
    ProductionSecurityServices,
    SQLiteAuditSink,
    SQLiteGrantProvider,
    _safe_regular_file,
    create_production_security_services,
    jwt_verifier_available,
)
from cgr.pulsate_api.security import (
    AuditUnavailable,
    AuthenticationRejected,
    AuthorizationPolicy,
    AuthorizationUnavailable,
    SecurityServices,
    StaticAuthenticator,
)
from cgr.pulsate_api.security_contracts import (
    AuditRecord,
    AuthenticatedPrincipal,
    ProtectedAction,
    ResourceGrant,
    utc_now,
)
from cgr.pulsate_api.experiments import ExperimentStore
from cgr.pulsate_api.natural_language import NaturalLanguageInterpretationStore
from cgr.pulsate_api.runs import RunCoordinator


SENTINEL_SECRET = "sentinel-production-secret-that-must-never-escape"


class _NoExecution:
    def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("Production security tests cannot execute runs.")


def _values(tmp_path: Path) -> dict[str, str]:
    data = tmp_path / "data"
    trusted = tmp_path / "trusted"
    data.mkdir(parents=True, exist_ok=True)
    trusted.mkdir(parents=True, exist_ok=True)
    (trusted / "jwks.json").write_text('{"keys":[]}', encoding="utf-8")
    return {
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
        "PULSATE_WORKFLOW_MAX_PARALLELISM": "8",
        "PULSATE_CATALOGUE_ENABLED": "false",
        "PULSATE_CATALOGUE_REQUIRED": "false",
        "PULSATE_CATALOGUE_MAXIMUM_BYTES": str(1024 * 1024),
        "PULSATE_CATALOGUE_MAXIMUM_ENTRIES": "256",
        "PULSATE_CATALOGUE_ALLOW_EMPTY": "true",
    }


def _configuration(tmp_path: Path):
    return load_production_configuration(MappingConfigurationSource(_values(tmp_path)))


def _principal(*, tenant: str = "tenant-alpha") -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject_identifier="scientist-alpha",
        tenant_identifier=tenant,
        authentication_method="oidc-jwt",
        scopes=(ProtectedAction.PROJECT_READ.value,),
        roles=("scientist",),
        audit_identifier="principal-alpha",
    )


def _grant(
    *,
    tenant: str = "tenant-alpha",
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
) -> ResourceGrant:
    return ResourceGrant(
        tenant_identifier=tenant,
        subject_identifier="scientist-alpha",
        resource_type="project",
        resource_identifier="project-alpha",
        allowed_actions=(ProtectedAction.PROJECT_READ,),
        valid_from=valid_from,
        valid_until=valid_until,
    )


def _audit(index: int = 1) -> AuditRecord:
    return AuditRecord(
        record_identifier=f"audit-{index}",
        event_time=utc_now(),
        correlation_identifier=f"correlation-{index}",
        request_identifier=f"request-{index}",
        principal_audit_identifier="principal-alpha",
        tenant_audit_identifier="sha256:" + "a" * 24,
        action=ProtectedAction.PROJECT_READ,
        resource_type="project",
        safe_resource_identity="sha256:" + "b" * 24,
        decision="allowed",
        reason_category="policy_allowed",
    )


def test_explicit_production_configuration_is_immutable_safe_and_deterministic(tmp_path: Path) -> None:
    first = _configuration(tmp_path)
    second = _configuration(tmp_path)

    assert first.environment == "production"
    assert first == second
    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64
    assert str(first.authorization.database_file).startswith(str(tmp_path.resolve()))
    assert first.workflow.maximum_parallelism == 8
    with pytest.raises(Exception):
        first.environment = "development"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("PULSATE_ENVIRONMENT", "staging"),
        ("PULSATE_AUTH_ISSUER", ""),
        ("PULSATE_AUTH_ALGORITHMS", "HS256"),
        ("PULSATE_GRANT_ALLOW_WILDCARDS", "yes"),
        ("PULSATE_WORKFLOW_MAX_PARALLELISM", "0"),
        ("PULSATE_WORKFLOW_MAX_PARALLELISM", "65"),
    ),
)
def test_invalid_or_missing_production_configuration_fails_closed(
    tmp_path: Path, field: str, value: str
) -> None:
    values = _values(tmp_path)
    values[field] = value
    with pytest.raises(ProductionConfigurationError) as error:
        load_production_configuration(MappingConfigurationSource(values))
    assert SENTINEL_SECRET not in str(error.value)
    assert str(tmp_path) not in str(error.value)


def test_unknown_and_oversized_configuration_inputs_fail_closed(tmp_path: Path) -> None:
    unknown = _values(tmp_path) | {"PULSATE_UNKNOWN_CRITICAL": "value"}
    with pytest.raises(ProductionConfigurationError, match="PULSATE_UNKNOWN_CRITICAL"):
        load_production_configuration(MappingConfigurationSource(unknown))
    oversized = _values(tmp_path) | {
        "PULSATE_AUTH_ISSUER": "x" * CONFIGURATION_INPUT_MAXIMUM_BYTES
    }
    with pytest.raises(ProductionConfigurationError, match="configuration_size"):
        load_production_configuration(MappingConfigurationSource(oversized))


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("PULSATE_AUTH_JWKS_FILE", "../outside.json"),
        ("PULSATE_GRANT_DATABASE_FILE", "../grants.sqlite3"),
        ("PULSATE_AUDIT_DATABASE_FILE", "../audit.sqlite3"),
    ),
)
def test_unsafe_configuration_paths_are_rejected(tmp_path: Path, field: str, value: str) -> None:
    values = _values(tmp_path)
    values[field] = value
    with pytest.raises(ProductionConfigurationError) as error:
        load_production_configuration(MappingConfigurationSource(values))
    assert str(tmp_path) not in str(error.value)


def test_absolute_database_path_outside_data_root_is_rejected(tmp_path: Path) -> None:
    values = _values(tmp_path)
    values["PULSATE_AUDIT_DATABASE_FILE"] = str(
        (tmp_path.parent / "outside-audit.sqlite3").resolve()
    )
    with pytest.raises(ProductionConfigurationError):
        load_production_configuration(MappingConfigurationSource(values))


def test_secret_wrapper_never_displays_or_serializes_the_secret() -> None:
    secret = SecretValue(SENTINEL_SECRET)
    assert secret.reveal() == SENTINEL_SECRET
    assert SENTINEL_SECRET not in repr(secret)
    assert SENTINEL_SECRET not in str(secret)
    with pytest.raises(ProductionConfigurationError) as error:
        SecretValue("")
    assert SENTINEL_SECRET not in str(error.value)


def test_symlinked_key_file_is_rejected_without_path_disclosure(tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text('{"keys":[]}', encoding="utf-8")
    link = trusted / "jwks.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("This platform cannot create a file symlink.")
    with pytest.raises(ProductionSecurityProviderError) as error:
        _safe_regular_file(link, trusted, maximum_bytes=1024)
    assert str(tmp_path) not in str(error.value)


def test_duplicate_jwks_key_identifiers_fail_before_snapshot_publication(tmp_path: Path) -> None:
    values = _values(tmp_path)
    Path(values["PULSATE_TRUSTED_CONFIGURATION_ROOT"], "jwks.json").write_text(
        json.dumps(
            {
                "keys": [
                    {"kid": "duplicate", "alg": "RS256", "kty": "RSA", "use": "sig", "n": "AQ", "e": "AQAB"},
                    {"kid": "duplicate", "alg": "RS256", "kty": "RSA", "use": "sig", "n": "AQ", "e": "AQAB"},
                ]
            }
        ),
        encoding="utf-8",
    )
    authenticator = OfflineJWKSAuthenticator(
        load_production_configuration(MappingConfigurationSource(values))
    )
    with pytest.raises(ProductionSecurityProviderError):
        authenticator.start()
    assert not authenticator.ready()


def test_declared_jwt_dependency_boundary_is_explicit() -> None:
    if jwt_verifier_available():
        pytest.skip("PyJWT is available; cryptographic behavior is covered below.")
    assert not jwt_verifier_available()


def _b64(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _jwt_material(tmp_path: Path) -> tuple[Any, dict[str, str]]:
    cryptography = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")
    private = cryptography.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = private.public_key().public_numbers()
    jwk = {
        "kid": "production-key-1",
        "kty": "RSA",
        "alg": "RS256",
        "use": "sig",
        "key_ops": ["verify"],
        "n": _b64(numbers.n),
        "e": _b64(numbers.e),
    }
    values = _values(tmp_path)
    Path(values["PULSATE_TRUSTED_CONFIGURATION_ROOT"], "jwks.json").write_text(
        json.dumps({"keys": [jwk]}), encoding="utf-8"
    )
    return private, values


@pytest.mark.skipif(not jwt_verifier_available(), reason="declared PyJWT dependency is not installed locally")
def test_valid_signed_jwt_authenticates_and_claim_mapping_is_deterministic(tmp_path: Path) -> None:
    import jwt

    private, values = _jwt_material(tmp_path)
    authenticator = OfflineJWKSAuthenticator(
        load_production_configuration(MappingConfigurationSource(values))
    )
    authenticator.start()
    now = utc_now()
    token = jwt.encode(
        {
            "iss": values["PULSATE_AUTH_ISSUER"],
            "aud": "pulsate-api",
            "sub": "scientist-alpha",
            "tenant_id": "tenant-alpha",
            "scope": "project.read scene.read",
            "roles": ["scientist"],
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        private,
        algorithm="RS256",
        headers={"kid": "production-key-1"},
    )
    principal = authenticator.authenticate(token, now=now)
    assert principal.subject_identifier == "scientist-alpha"
    assert principal.tenant_identifier == "tenant-alpha"
    assert principal.scopes == ("project.read", "scene.read")
    assert token not in repr(principal)


@pytest.mark.skipif(not jwt_verifier_available(), reason="declared PyJWT dependency is not installed locally")
@pytest.mark.parametrize(
    "mutation",
    (
        "wrong_issuer",
        "wrong_audience",
        "expired",
        "future_nbf",
        "future_iat",
        "missing_subject",
        "missing_tenant",
        "unknown_kid",
        "missing_kid",
    ),
)
def test_invalid_jwt_claims_and_unknown_keys_fail_closed(tmp_path: Path, mutation: str) -> None:
    import jwt

    private, values = _jwt_material(tmp_path)
    authenticator = OfflineJWKSAuthenticator(_configuration_from(values))
    authenticator.start()
    now = utc_now()
    claims: dict[str, Any] = {
        "iss": values["PULSATE_AUTH_ISSUER"], "aud": "pulsate-api", "sub": "scientist-alpha",
        "tenant_id": "tenant-alpha", "iat": now, "exp": now + timedelta(minutes=5),
    }
    headers = {"kid": "production-key-1"}
    if mutation == "wrong_issuer":
        claims["iss"] = "https://wrong.example/"
    if mutation == "wrong_audience":
        claims["aud"] = "wrong"
    if mutation == "expired":
        claims["exp"] = now - timedelta(minutes=1)
    if mutation == "future_nbf":
        claims["nbf"] = now + timedelta(minutes=10)
    if mutation == "future_iat":
        claims["iat"] = now + timedelta(minutes=10)
    if mutation == "missing_subject":
        claims.pop("sub")
    if mutation == "missing_tenant":
        claims.pop("tenant_id")
    if mutation == "unknown_kid":
        headers["kid"] = "unknown"
    if mutation == "missing_kid":
        headers = {}
    token = jwt.encode(claims, private, algorithm="RS256", headers=headers)
    with pytest.raises(AuthenticationRejected):
        authenticator.authenticate(token, now=now)


def _configuration_from(values: dict[str, str]):
    return load_production_configuration(MappingConfigurationSource(values))


@pytest.mark.skipif(not jwt_verifier_available(), reason="declared PyJWT dependency is not installed locally")
def test_unsigned_disallowed_oversized_and_invalid_signature_tokens_fail(tmp_path: Path) -> None:
    import jwt

    private, values = _jwt_material(tmp_path)
    authenticator = OfflineJWKSAuthenticator(_configuration_from(values))
    authenticator.start()
    now = utc_now()
    claims = {"iss": values["PULSATE_AUTH_ISSUER"], "aud": "pulsate-api", "sub": "s", "tenant_id": "t", "iat": now, "exp": now + timedelta(minutes=5)}
    unsigned = jwt.encode(claims, key="", algorithm="none", headers={"kid": "production-key-1"})
    confused = jwt.encode(
        claims,
        key="test-only-hs256-algorithm-confusion-key",
        algorithm="HS256",
        headers={"kid": "production-key-1"},
    )
    wrong_private, _ = _jwt_material(tmp_path / "other")
    invalid = jwt.encode(claims, wrong_private, algorithm="RS256", headers={"kid": "production-key-1"})
    for token in (unsigned, confused, invalid, "x" * 9000, "not.compact"):
        with pytest.raises(AuthenticationRejected):
            authenticator.authenticate(token, now=now)


@pytest.mark.skipif(not jwt_verifier_available(), reason="declared PyJWT dependency is not installed locally")
def test_oversized_jwt_is_rejected_before_header_or_signature_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, values = _jwt_material(tmp_path)
    authenticator = OfflineJWKSAuthenticator(_configuration_from(values))
    authenticator.start()

    def forbidden_header_parse(_token: str) -> dict[str, object]:
        raise AssertionError("Oversized tokens must fail before JWT parsing.")

    import jwt

    monkeypatch.setattr(jwt, "get_unverified_header", forbidden_header_parse)
    with pytest.raises(AuthenticationRejected, match="oversized_credential"):
        authenticator.authenticate("x" * 9000, now=utc_now())


@pytest.mark.skipif(not jwt_verifier_available(), reason="declared PyJWT dependency is not installed locally")
def test_jwks_reload_is_atomic_and_failed_reload_preserves_previous_snapshot(tmp_path: Path) -> None:
    import jwt

    private, values = _jwt_material(tmp_path)
    configuration = _configuration_from(values)
    authenticator = OfflineJWKSAuthenticator(configuration)
    authenticator.start()
    now = utc_now()
    claims = {
        "iss": values["PULSATE_AUTH_ISSUER"],
        "aud": "pulsate-api",
        "sub": "scientist-alpha",
        "tenant_id": "tenant-alpha",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    original = jwt.encode(
        claims,
        private,
        algorithm="RS256",
        headers={"kid": "production-key-1"},
    )
    key_file = configuration.authentication.jwks_file
    key_file.write_text('{"keys":[]}', encoding="utf-8")
    with pytest.raises(ProductionSecurityProviderError):
        authenticator.reload()
    assert authenticator.authenticate(original, now=now).subject_identifier == "scientist-alpha"

    replacement_private, replacement_values = _jwt_material(tmp_path / "replacement")
    replacement_document = Path(
        replacement_values["PULSATE_TRUSTED_CONFIGURATION_ROOT"], "jwks.json"
    ).read_text(encoding="utf-8")
    replacement_document = replacement_document.replace("production-key-1", "production-key-2")
    key_file.write_text(replacement_document, encoding="utf-8")
    authenticator.reload()
    replacement = jwt.encode(
        claims,
        replacement_private,
        algorithm="RS256",
        headers={"kid": "production-key-2"},
    )
    assert authenticator.authenticate(replacement, now=now).subject_identifier == "scientist-alpha"
    with pytest.raises(AuthenticationRejected):
        authenticator.authenticate(original, now=now)


def test_grants_survive_restart_and_duplicate_creation_is_deterministic(tmp_path: Path) -> None:
    path = tmp_path / "grants.sqlite3"
    first = SQLiteGrantProvider(path)
    first.start()
    first.create_grant("grant-alpha", _grant())
    first.create_grant("grant-alpha", _grant())
    first.close()
    second = SQLiteGrantProvider(path)
    second.start()
    assert second.grants_for(_principal()) == (_grant(),)
    with pytest.raises(ProductionSecurityProviderError):
        second.create_grant("grant-alpha", _grant(tenant="tenant-other"))
    second.close()


def test_grant_policy_denies_cross_tenant_expired_future_and_revoked_grants(tmp_path: Path) -> None:
    now = utc_now()
    cases = (
        ("valid", _grant(), False, True),
        ("expired", _grant(valid_until=now - timedelta(seconds=1)), False, False),
        ("future", _grant(valid_from=now + timedelta(hours=1)), False, False),
        ("revoked", _grant(), True, False),
    )
    for name, grant, revoke, expected in cases:
        provider = SQLiteGrantProvider(tmp_path / f"{name}.sqlite3")
        provider.start()
        provider.create_grant(f"grant-{name}", grant)
        if revoke:
            provider.revoke(f"grant-{name}")
        policy = AuthorizationPolicy(provider)
        assert policy.authorize(
            _principal(),
            ProtectedAction.PROJECT_READ,
            "project",
            "project-alpha",
            now=now,
        ) is expected
        assert not policy.authorize(
            _principal(tenant="tenant-other"),
            ProtectedAction.PROJECT_READ,
            "project",
            "project-alpha",
            now=now,
        )
        provider.close()


def test_concurrent_grant_readers_are_isolated_and_shutdown_is_controlled(tmp_path: Path) -> None:
    provider = SQLiteGrantProvider(tmp_path / "grants.sqlite3")
    provider.start()
    provider.create_grant("grant-alpha", _grant())
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: provider.grants_for(_principal()), range(32)))
    assert all(result == (_grant(),) for result in results)
    provider.close()
    with pytest.raises(AuthorizationUnavailable):
        provider.grants_for(_principal())


def test_incompatible_grant_schema_fails_without_exposing_database_path(tmp_path: Path) -> None:
    path = tmp_path / "private-grants.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE schema_metadata (component TEXT PRIMARY KEY, version INTEGER NOT NULL)")
    connection.execute("INSERT INTO schema_metadata VALUES ('grants', 999)")
    connection.commit()
    connection.close()
    with pytest.raises(ProductionSecurityProviderError) as error:
        SQLiteGrantProvider(path).start()
    assert str(path) not in str(error.value)


def test_audit_records_survive_restart_and_chain_verifies(tmp_path: Path) -> None:
    path = tmp_path / "audit.sqlite3"
    first = SQLiteAuditSink(path)
    first.start()
    first.append(_audit(1))
    first.append(_audit(2))
    assert first.verify_integrity()
    first.close()
    second = SQLiteAuditSink(path)
    second.start()
    assert second.verify_integrity()
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT previous_fingerprint FROM audit_records WHERE sequence_number=1").fetchone() == (AUDIT_GENESIS_FINGERPRINT,)
    connection.close()
    second.close()


@pytest.mark.parametrize("mutation", ("record", "delete", "state"))
def test_audit_mutation_deletion_and_head_tampering_are_detected(tmp_path: Path, mutation: str) -> None:
    path = tmp_path / "audit.sqlite3"
    sink = SQLiteAuditSink(path)
    sink.start()
    sink.append(_audit(1))
    sink.append(_audit(2))
    sink.close()
    connection = sqlite3.connect(path)
    if mutation == "record":
        connection.execute("UPDATE audit_records SET record_json='{}' WHERE sequence_number=1")
    elif mutation == "delete":
        connection.execute("DELETE FROM audit_records WHERE sequence_number=2")
    else:
        connection.execute("UPDATE audit_state SET head_fingerprint=?", ("f" * 64,))
    connection.commit()
    connection.close()
    with pytest.raises(ProductionSecurityProviderError):
        SQLiteAuditSink(path).start()


def test_failed_audit_append_is_atomic_and_concurrent_appends_preserve_chain(tmp_path: Path) -> None:
    sink = SQLiteAuditSink(tmp_path / "audit.sqlite3")
    sink.start()
    first = _audit(1)
    sink.append(first)
    with pytest.raises(AuditUnavailable):
        sink.append(first)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda index: sink.append(_audit(index)), range(2, 34)))
    assert sink.verify_integrity()
    sink.close()


def test_audit_storage_contains_only_safe_contract_data(tmp_path: Path) -> None:
    path = tmp_path / "audit.sqlite3"
    sink = SQLiteAuditSink(path)
    sink.start()
    sink.append(_audit())
    sink.close()
    assert SENTINEL_SECRET.encode() not in path.read_bytes()


def test_incompatible_audit_schema_fails_without_exposing_database_path(tmp_path: Path) -> None:
    path = tmp_path / "private-audit.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE schema_metadata (component TEXT PRIMARY KEY, version INTEGER NOT NULL)")
    connection.execute("INSERT INTO schema_metadata VALUES ('audit', 999)")
    connection.commit()
    connection.close()
    with pytest.raises(ProductionSecurityProviderError) as error:
        SQLiteAuditSink(path).start()
    assert str(path) not in str(error.value)


def test_complete_production_services_lifecycle_is_deterministic(tmp_path: Path) -> None:
    configuration = _configuration(tmp_path)
    services = ProductionSecurityServices(
        configuration,
        authenticator=StaticAuthenticator({"token": _principal()}),
    )
    services.start()
    services.start()
    assert services.ready()
    assert all(services.diagnostic_snapshot().values())
    services.close()
    services.close()
    assert not services.ready()


def test_partial_startup_failure_closes_initialized_authenticator(tmp_path: Path) -> None:
    class TrackingAuthenticator(StaticAuthenticator):
        closed = False

        def close(self) -> None:
            self.closed = True
            super().close()

    authenticator = TrackingAuthenticator({"token": _principal()})
    security = SecurityServices(
        authenticator=authenticator,
        grant_provider=SQLiteGrantProvider(tmp_path / "directory"),
        audit_sink=SQLiteAuditSink(tmp_path / "audit.sqlite3"),
    )
    (tmp_path / "directory").mkdir()
    with pytest.raises(Exception):
        security.start()
    assert authenticator.closed
    assert not security.ready()


def test_configuration_check_reports_only_safe_status_and_closes_services(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    instances: list[ProductionSecurityServices] = []

    def factory(configuration):
        service = ProductionSecurityServices(
            configuration,
            authenticator=StaticAuthenticator({"token": _principal()}),
        )
        instances.append(service)
        return service

    source = MappingConfigurationSource(_values(tmp_path))
    result = validate_configuration(source, services_factory=factory)
    assert result["status"] == "ready"
    assert len(result["configuration_fingerprint"]) == 64
    assert not instances[0].ready()
    assert configuration_check_main([], source=source, services_factory=factory) == 0
    output = capsys.readouterr().out
    assert '"status":"ready"' in output
    assert SENTINEL_SECRET not in output
    assert str(tmp_path) not in output


@pytest.mark.skipif(not jwt_verifier_available(), reason="declared PyJWT dependency is not installed locally")
def test_real_production_configuration_check_initializes_keys_and_durable_schemas(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, values = _jwt_material(tmp_path)
    listener_calls = 0

    def forbidden_listener(*_args: object, **_kwargs: object) -> None:
        nonlocal listener_calls
        listener_calls += 1
        raise AssertionError("Configuration validation cannot start a listener.")

    import socket

    monkeypatch.setattr(socket.socket, "listen", forbidden_listener)
    assert configuration_check_main(
        [], source=MappingConfigurationSource(values)
    ) == 0
    output = capsys.readouterr().out
    assert '"status":"ready"' in output
    assert listener_calls == 0
    assert Path(values["PULSATE_APPLICATION_DATA_ROOT"], "security", "grants.sqlite3").is_file()
    assert Path(values["PULSATE_APPLICATION_DATA_ROOT"], "security", "audit.sqlite3").is_file()


def test_invalid_configuration_check_exits_nonzero_without_internal_details(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    values = _values(tmp_path)
    values.pop("PULSATE_AUTH_ISSUER")
    assert configuration_check_main([], source=MappingConfigurationSource(values)) == 1
    captured = capsys.readouterr()
    assert "configuration_validation_failed" in captured.err
    assert str(tmp_path) not in captured.err


def test_configuration_and_provider_logs_never_contain_sentinel_secret(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="cgr.pulsate.security")
    configuration = _configuration(tmp_path)
    services = create_production_security_services(
        configuration,
        authenticator_factory=lambda _: StaticAuthenticator(
            {SENTINEL_SECRET: _principal()}
        ),
    )
    services.start()
    services.close()
    assert SENTINEL_SECRET not in "\n".join(record.message for record in caplog.records)


def _application(tmp_path: Path, services: SecurityServices):
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
            unavailable_reason="disabled for production security tests",
        ),
        security_services=services,
    )


def test_liveness_remains_healthy_and_readiness_is_coarse_and_path_free(tmp_path: Path) -> None:
    configuration = _configuration(tmp_path)
    services = ProductionSecurityServices(
        configuration,
        authenticator=StaticAuthenticator({"token": _principal()}),
    )
    with TestClient(_application(tmp_path, services)) as client:
        live = client.get("/live")
        ready = client.get("/ready")
    assert live.status_code == 200
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ready",
        "components": {
            "application": "ready",
            "security": "ready",
            "repositories": "ready",
            "catalogue": "ready",
        },
    }
    assert str(tmp_path) not in ready.text
    assert SENTINEL_SECRET not in ready.text


def test_invalid_production_keys_keep_readiness_unavailable_but_not_liveness(tmp_path: Path) -> None:
    configuration = _configuration(tmp_path)
    services = ProductionSecurityServices(configuration)
    with TestClient(_application(tmp_path, services)) as client:
        live = client.get("/live")
        ready = client.get("/ready")
    assert live.status_code == 200
    assert ready.status_code == 503
    assert ready.json()["detail"]["code"] == "service_not_ready"
    assert str(tmp_path) not in ready.text


@pytest.mark.parametrize("unavailable_component", ("grant", "audit"))
def test_durable_store_failure_prevents_readiness_and_closes_partial_startup(
    tmp_path: Path, unavailable_component: str
) -> None:
    configuration = _configuration(tmp_path)
    blocked_path = (
        configuration.authorization.database_file
        if unavailable_component == "grant"
        else configuration.audit.database_file
    )
    blocked_path.parent.mkdir(parents=True, exist_ok=True)
    blocked_path.mkdir()
    services = ProductionSecurityServices(
        configuration,
        authenticator=StaticAuthenticator({"token": _principal()}),
    )
    with TestClient(_application(tmp_path, services)) as client:
        assert client.get("/live").status_code == 200
        ready = client.get("/ready")
    assert ready.status_code == 503
    assert ready.json()["detail"]["code"] == "service_not_ready"
    assert not services.ready()
    assert str(tmp_path) not in ready.text
