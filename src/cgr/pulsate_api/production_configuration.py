"""Validated, secret-safe production configuration for the Pulsate API."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion


CONFIGURATION_SCHEMA_VERSION = "cgr.pulsate-runtime-configuration/1.0.0"
CONFIGURATION_INPUT_MAXIMUM_BYTES = 64 * 1024
SECRET_MAXIMUM_BYTES = 16 * 1024


class ProductionConfigurationError(ValueError):
    """Controlled configuration failure with no secret or path disclosure."""

    def __init__(self, field: str = "configuration") -> None:
        super().__init__(f"Production configuration field '{field}' is invalid.")
        self.field = field


class ConfigurationSource(Protocol):
    def load(self) -> Mapping[str, str]: ...


class MappingConfigurationSource:
    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)

    def load(self) -> Mapping[str, str]:
        return dict(self._values)


class EnvironmentConfigurationSource:
    def __init__(self, environment: Mapping[str, str] | None = None) -> None:
        self._environment = environment if environment is not None else os.environ

    def load(self) -> Mapping[str, str]:
        return {
            name: self._environment[name]
            for name in PRODUCTION_ENVIRONMENT_VARIABLES
            if name in self._environment
        }


class _ConfigurationContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AuthenticationConfiguration(_ConfigurationContract):
    issuer: str = Field(min_length=1, max_length=512)
    audiences: tuple[str, ...] = Field(min_length=1, max_length=16)
    algorithms: tuple[Literal["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"], ...] = Field(
        min_length=1,
        max_length=6,
    )
    jwks_file: Path
    subject_claim: str = Field(default="sub", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    tenant_claim: str = Field(default="tenant_id", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    scope_claim: str = Field(default="scope", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    roles_claim: str = Field(default="roles", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    clock_skew_seconds: int = Field(default=60, ge=0, le=300)
    maximum_token_bytes: int = Field(default=8192, ge=256, le=65536)

    @field_validator("audiences", "algorithms", mode="before")
    @classmethod
    def normalize_sequences(cls, value: object) -> object:
        if isinstance(value, (list, tuple)):
            return tuple(sorted(set(value)))
        return value


class AuthorizationConfiguration(_ConfigurationContract):
    database_file: Path
    busy_timeout_ms: int = Field(default=5000, ge=100, le=30000)
    allow_wildcards: bool = False


class AuditConfiguration(_ConfigurationContract):
    database_file: Path
    mandatory: bool = True
    read_fail_closed: bool = False
    busy_timeout_ms: int = Field(default=5000, ge=100, le=30000)
    verification_maximum_records: int = Field(default=100000, ge=1, le=1000000)


class ObservabilityConfiguration(_ConfigurationContract):
    service_name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class RepositoryConfiguration(_ConfigurationContract):
    application_data_root: Path
    trusted_configuration_root: Path


class CapabilityCatalogueConfiguration(_ConfigurationContract):
    enabled: bool
    required: bool
    catalogue_file: Path | None = None
    expected_identifier: str | None = Field(
        default=None,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    )
    expected_version: CapabilityVersion | None = None
    expected_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    maximum_bytes: int = Field(ge=1024, le=16 * 1024 * 1024)
    maximum_entries: int = Field(ge=1, le=256)
    allow_empty: bool

    @model_validator(mode="after")
    def validate_state(self) -> CapabilityCatalogueConfiguration:
        expectations = (
            self.catalogue_file,
            self.expected_identifier,
            self.expected_version,
            self.expected_fingerprint,
        )
        if self.enabled and any(item is None for item in expectations):
            raise ValueError("Enabled production catalogue configuration is incomplete.")
        if not self.enabled and any(item is not None for item in expectations):
            raise ValueError("Disabled production catalogue cannot declare source expectations.")
        if self.required and not self.enabled:
            raise ValueError("A required production catalogue must be enabled.")
        if not self.enabled and not self.allow_empty:
            raise ValueError("A disabled production catalogue must explicitly allow empty state.")
        return self


class SecurityLimitConfiguration(_ConfigurationContract):
    configuration_maximum_bytes: int = Field(
        default=CONFIGURATION_INPUT_MAXIMUM_BYTES,
        ge=1024,
        le=1024 * 1024,
    )
    secret_maximum_bytes: int = Field(default=SECRET_MAXIMUM_BYTES, ge=32, le=65536)


class ReadinessConfiguration(_ConfigurationContract):
    require_authenticator: bool = True
    require_grant_store: bool = True
    require_audit_store: bool = True
    require_repositories: bool = True


class PulsateRuntimeConfiguration(_ConfigurationContract):
    schema_version: Literal["cgr.pulsate-runtime-configuration/1.0.0"] = CONFIGURATION_SCHEMA_VERSION
    environment: Literal["test", "development", "production"]
    service_identity: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    authentication: AuthenticationConfiguration
    authorization: AuthorizationConfiguration
    audit: AuditConfiguration
    observability: ObservabilityConfiguration
    repositories: RepositoryConfiguration
    catalogue: CapabilityCatalogueConfiguration
    limits: SecurityLimitConfiguration = SecurityLimitConfiguration()
    readiness: ReadinessConfiguration = ReadinessConfiguration()

    @model_validator(mode="after")
    def validate_production(self) -> PulsateRuntimeConfiguration:
        if self.environment != "production":
            raise ValueError("The production runtime requires an explicit production environment.")
        if not self.audit.mandatory and self.readiness.require_audit_store:
            raise ValueError("Mandatory readiness requires the audit store.")
        return self

    def safe_dict(self) -> dict[str, object]:
        safe = self.model_dump(mode="json")
        authentication = dict(safe["authentication"])
        authentication["jwks_file"] = "configured-path"
        authorization = dict(safe["authorization"])
        authorization["database_file"] = "configured-path"
        audit = dict(safe["audit"])
        audit["database_file"] = "configured-path"
        repositories = dict(safe["repositories"])
        repositories["application_data_root"] = "configured-path"
        repositories["trusted_configuration_root"] = "configured-path"
        catalogue = dict(safe["catalogue"])
        if catalogue["catalogue_file"] is not None:
            catalogue["catalogue_file"] = "configured-path"
        safe.update(
            authentication=authentication,
            authorization=authorization,
            audit=audit,
            repositories=repositories,
            catalogue=catalogue,
        )
        return safe

    def canonical_safe_json(self) -> str:
        return json.dumps(self.safe_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_safe_json().encode("utf-8")).hexdigest()


class SecretValue:
    """Bounded secret whose display and representation never expose its value."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not value or len(value.encode("utf-8")) > SECRET_MAXIMUM_BYTES:
            raise ProductionConfigurationError("secret")
        self._value = SecretStr(value)

    def reveal(self) -> str:
        return self._value.get_secret_value()

    def __repr__(self) -> str:
        return "SecretValue('**********')"

    def __str__(self) -> str:
        return "**********"


PRODUCTION_ENVIRONMENT_VARIABLES = (
    "PULSATE_ENVIRONMENT",
    "PULSATE_SERVICE_IDENTITY",
    "PULSATE_APPLICATION_DATA_ROOT",
    "PULSATE_TRUSTED_CONFIGURATION_ROOT",
    "PULSATE_AUTH_ISSUER",
    "PULSATE_AUTH_AUDIENCES",
    "PULSATE_AUTH_ALGORITHMS",
    "PULSATE_AUTH_JWKS_FILE",
    "PULSATE_AUTH_SUBJECT_CLAIM",
    "PULSATE_AUTH_TENANT_CLAIM",
    "PULSATE_AUTH_SCOPE_CLAIM",
    "PULSATE_AUTH_ROLES_CLAIM",
    "PULSATE_AUTH_CLOCK_SKEW_SECONDS",
    "PULSATE_AUTH_MAXIMUM_TOKEN_BYTES",
    "PULSATE_GRANT_DATABASE_FILE",
    "PULSATE_GRANT_BUSY_TIMEOUT_MS",
    "PULSATE_GRANT_ALLOW_WILDCARDS",
    "PULSATE_AUDIT_DATABASE_FILE",
    "PULSATE_AUDIT_MANDATORY",
    "PULSATE_AUDIT_READ_FAIL_CLOSED",
    "PULSATE_AUDIT_BUSY_TIMEOUT_MS",
    "PULSATE_AUDIT_VERIFICATION_MAXIMUM_RECORDS",
    "PULSATE_CATALOGUE_ENABLED",
    "PULSATE_CATALOGUE_REQUIRED",
    "PULSATE_CATALOGUE_FILE",
    "PULSATE_CATALOGUE_EXPECTED_IDENTIFIER",
    "PULSATE_CATALOGUE_EXPECTED_VERSION",
    "PULSATE_CATALOGUE_EXPECTED_FINGERPRINT",
    "PULSATE_CATALOGUE_MAXIMUM_BYTES",
    "PULSATE_CATALOGUE_MAXIMUM_ENTRIES",
    "PULSATE_CATALOGUE_ALLOW_EMPTY",
)


def _boolean(value: str, field: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise ProductionConfigurationError(field)


def _integer(value: str, field: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise ProductionConfigurationError(field) from None


def _required(values: Mapping[str, str], name: str) -> str:
    value = values.get(name)
    if value is None or not value.strip():
        raise ProductionConfigurationError(name)
    return value.strip()


def _absolute_root(value: str, field: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ProductionConfigurationError(field)
    normalized = Path(os.path.abspath(path))
    try:
        metadata = normalized.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise OSError
        return normalized.resolve(strict=True)
    except OSError:
        raise ProductionConfigurationError(field) from None


def _controlled_path(value: str, root: Path, field: str) -> Path:
    path = Path(value)
    if ".." in path.parts:
        raise ProductionConfigurationError(field)
    candidate = path if path.is_absolute() else root / path
    normalized = Path(os.path.abspath(candidate))
    try:
        normalized.relative_to(root)
    except ValueError:
        raise ProductionConfigurationError(field) from None
    return normalized


def load_production_configuration(source: ConfigurationSource) -> PulsateRuntimeConfiguration:
    values = dict(source.load())
    serialized_size = len(json.dumps(values, ensure_ascii=False).encode("utf-8"))
    if serialized_size > CONFIGURATION_INPUT_MAXIMUM_BYTES:
        raise ProductionConfigurationError("configuration_size")
    unknown = set(values) - set(PRODUCTION_ENVIRONMENT_VARIABLES)
    if unknown:
        raise ProductionConfigurationError(sorted(unknown)[0])
    if _required(values, "PULSATE_ENVIRONMENT") != "production":
        raise ProductionConfigurationError("PULSATE_ENVIRONMENT")
    data_root = _absolute_root(_required(values, "PULSATE_APPLICATION_DATA_ROOT"), "PULSATE_APPLICATION_DATA_ROOT")
    config_root = _absolute_root(
        _required(values, "PULSATE_TRUSTED_CONFIGURATION_ROOT"),
        "PULSATE_TRUSTED_CONFIGURATION_ROOT",
    )
    try:
        return PulsateRuntimeConfiguration(
            environment="production",
            service_identity=_required(values, "PULSATE_SERVICE_IDENTITY"),
            authentication=AuthenticationConfiguration(
                issuer=_required(values, "PULSATE_AUTH_ISSUER"),
                audiences=tuple(
                    item.strip()
                    for item in _required(values, "PULSATE_AUTH_AUDIENCES").split(",")
                    if item.strip()
                ),
                algorithms=tuple(
                    item.strip()
                    for item in _required(values, "PULSATE_AUTH_ALGORITHMS").split(",")
                    if item.strip()
                ),
                jwks_file=_controlled_path(_required(values, "PULSATE_AUTH_JWKS_FILE"), config_root, "PULSATE_AUTH_JWKS_FILE"),
                subject_claim=values.get("PULSATE_AUTH_SUBJECT_CLAIM", "sub"),
                tenant_claim=values.get("PULSATE_AUTH_TENANT_CLAIM", "tenant_id"),
                scope_claim=values.get("PULSATE_AUTH_SCOPE_CLAIM", "scope"),
                roles_claim=values.get("PULSATE_AUTH_ROLES_CLAIM", "roles"),
                clock_skew_seconds=_integer(values.get("PULSATE_AUTH_CLOCK_SKEW_SECONDS", "60"), "PULSATE_AUTH_CLOCK_SKEW_SECONDS"),
                maximum_token_bytes=_integer(values.get("PULSATE_AUTH_MAXIMUM_TOKEN_BYTES", "8192"), "PULSATE_AUTH_MAXIMUM_TOKEN_BYTES"),
            ),
            authorization=AuthorizationConfiguration(
                database_file=_controlled_path(_required(values, "PULSATE_GRANT_DATABASE_FILE"), data_root, "PULSATE_GRANT_DATABASE_FILE"),
                busy_timeout_ms=_integer(values.get("PULSATE_GRANT_BUSY_TIMEOUT_MS", "5000"), "PULSATE_GRANT_BUSY_TIMEOUT_MS"),
                allow_wildcards=_boolean(values.get("PULSATE_GRANT_ALLOW_WILDCARDS", "false"), "PULSATE_GRANT_ALLOW_WILDCARDS"),
            ),
            audit=AuditConfiguration(
                database_file=_controlled_path(_required(values, "PULSATE_AUDIT_DATABASE_FILE"), data_root, "PULSATE_AUDIT_DATABASE_FILE"),
                mandatory=_boolean(values.get("PULSATE_AUDIT_MANDATORY", "true"), "PULSATE_AUDIT_MANDATORY"),
                read_fail_closed=_boolean(values.get("PULSATE_AUDIT_READ_FAIL_CLOSED", "false"), "PULSATE_AUDIT_READ_FAIL_CLOSED"),
                busy_timeout_ms=_integer(values.get("PULSATE_AUDIT_BUSY_TIMEOUT_MS", "5000"), "PULSATE_AUDIT_BUSY_TIMEOUT_MS"),
                verification_maximum_records=_integer(values.get("PULSATE_AUDIT_VERIFICATION_MAXIMUM_RECORDS", "100000"), "PULSATE_AUDIT_VERIFICATION_MAXIMUM_RECORDS"),
            ),
            observability=ObservabilityConfiguration(service_name=_required(values, "PULSATE_SERVICE_IDENTITY")),
            repositories=RepositoryConfiguration(application_data_root=data_root, trusted_configuration_root=config_root),
            catalogue=_catalogue_configuration(values, config_root),
        )
    except ProductionConfigurationError:
        raise
    except Exception:
        raise ProductionConfigurationError("configuration") from None


def _catalogue_configuration(
    values: Mapping[str, str],
    trusted_root: Path,
) -> CapabilityCatalogueConfiguration:
    enabled = _boolean(
        _required(values, "PULSATE_CATALOGUE_ENABLED"),
        "PULSATE_CATALOGUE_ENABLED",
    )
    required = _boolean(
        _required(values, "PULSATE_CATALOGUE_REQUIRED"),
        "PULSATE_CATALOGUE_REQUIRED",
    )
    allow_empty = _boolean(
        _required(values, "PULSATE_CATALOGUE_ALLOW_EMPTY"),
        "PULSATE_CATALOGUE_ALLOW_EMPTY",
    )
    expected_identifier = values.get("PULSATE_CATALOGUE_EXPECTED_IDENTIFIER")
    expected_version = values.get("PULSATE_CATALOGUE_EXPECTED_VERSION")
    expected_fingerprint = values.get("PULSATE_CATALOGUE_EXPECTED_FINGERPRINT")
    try:
        return CapabilityCatalogueConfiguration(
            enabled=enabled,
            required=required,
            catalogue_file=(
                _controlled_path(
                    _required(values, "PULSATE_CATALOGUE_FILE"),
                    trusted_root,
                    "PULSATE_CATALOGUE_FILE",
                )
                if enabled
                else None
            ),
            expected_identifier=(
                _required(values, "PULSATE_CATALOGUE_EXPECTED_IDENTIFIER")
                if enabled
                else expected_identifier
            ),
            expected_version=(
                CapabilityVersion.parse(
                    _required(values, "PULSATE_CATALOGUE_EXPECTED_VERSION")
                )
                if enabled
                else (
                    CapabilityVersion.parse(expected_version)
                    if expected_version is not None
                    else None
                )
            ),
            expected_fingerprint=(
                _required(values, "PULSATE_CATALOGUE_EXPECTED_FINGERPRINT")
                if enabled
                else expected_fingerprint
            ),
            maximum_bytes=_integer(
                _required(values, "PULSATE_CATALOGUE_MAXIMUM_BYTES"),
                "PULSATE_CATALOGUE_MAXIMUM_BYTES",
            ),
            maximum_entries=_integer(
                _required(values, "PULSATE_CATALOGUE_MAXIMUM_ENTRIES"),
                "PULSATE_CATALOGUE_MAXIMUM_ENTRIES",
            ),
            allow_empty=allow_empty,
        )
    except ProductionConfigurationError:
        raise
    except Exception:
        raise ProductionConfigurationError("catalogue") from None
