"""Stable enterprise security and audit contracts for Pulsate."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SECURITY_CONTRACT_VERSION = "cgr.pulsate-security/1.0.0"
AUDIT_CONTRACT_VERSION = "cgr.pulsate-audit/1.0.0"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")

BoundedIdentifier = Annotated[str, Field(min_length=1, max_length=128)]
BoundedName = Annotated[str, Field(min_length=1, max_length=128)]


def _validate_identifier(value: str, field: str) -> str:
    if not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"{field} is invalid.")
    return value


class _FrozenContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class ProtectedAction(StrEnum):
    PROJECT_READ = "project.read"
    ARTIFACT_READ = "artifact.read"
    SCENE_READ = "scene.read"
    PLANNING_EVALUATE = "planning.evaluate"
    RUN_READ = "run.read"
    EXPERIMENT_READ = "experiment.read"
    EXECUTION_REQUEST = "execution.request"
    EXECUTION_APPROVE = "execution.approve"
    ADMINISTRATION = "administration"


class AuthenticatedPrincipal(_FrozenContract):
    schema_version: Literal["cgr.pulsate-security/1.0.0"] = SECURITY_CONTRACT_VERSION
    subject_identifier: BoundedIdentifier
    tenant_identifier: BoundedIdentifier
    authentication_method: BoundedName
    scopes: tuple[BoundedName, ...] = Field(max_length=128)
    roles: tuple[BoundedName, ...] = Field(default=(), max_length=64)
    audit_identifier: BoundedIdentifier

    @field_validator("scopes", "roles", mode="before")
    @classmethod
    def order_authorities(cls, value: object) -> object:
        if isinstance(value, (list, tuple, set, frozenset)):
            items = tuple(value)
            if all(isinstance(item, str) for item in items):
                return tuple(sorted(set(items)))
        return value

    @model_validator(mode="after")
    def validate_identity(self) -> AuthenticatedPrincipal:
        for field in (
            "subject_identifier",
            "tenant_identifier",
            "authentication_method",
            "audit_identifier",
        ):
            _validate_identifier(getattr(self, field), field)
        scopes = tuple(sorted(set(self.scopes)))
        roles = tuple(sorted(set(self.roles)))
        if scopes != self.scopes or roles != self.roles:
            raise ValueError("Principal scopes and roles must be unique and sorted.")
        for value in (*scopes, *roles):
            _validate_identifier(value, "scope or role")
        return self


class RequestSecurityContext(_FrozenContract):
    schema_version: Literal["cgr.pulsate-security/1.0.0"] = SECURITY_CONTRACT_VERSION
    principal: AuthenticatedPrincipal
    correlation_identifier: BoundedIdentifier
    request_identifier: BoundedIdentifier
    authenticated_at: datetime

    @model_validator(mode="after")
    def validate_context(self) -> RequestSecurityContext:
        _validate_identifier(self.correlation_identifier, "correlation_identifier")
        _validate_identifier(self.request_identifier, "request_identifier")
        if self.authenticated_at.tzinfo is None:
            raise ValueError("Authentication time must be timezone-aware.")
        return self


class ResourceGrant(_FrozenContract):
    schema_version: Literal["cgr.pulsate-security/1.0.0"] = SECURITY_CONTRACT_VERSION
    tenant_identifier: BoundedIdentifier
    subject_identifier: BoundedIdentifier | None = None
    role: BoundedName | None = None
    resource_type: BoundedName
    resource_identifier: BoundedIdentifier
    allowed_actions: tuple[ProtectedAction, ...] = Field(min_length=1, max_length=16)
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    @field_validator("allowed_actions", mode="before")
    @classmethod
    def order_actions(cls, value: object) -> object:
        if isinstance(value, (list, tuple, set, frozenset)):
            items = tuple(value)
            if all(isinstance(item, str) for item in items):
                return tuple(sorted(set(items), key=str))
        return value

    @model_validator(mode="after")
    def validate_grant(self) -> ResourceGrant:
        for field in ("tenant_identifier", "resource_type"):
            _validate_identifier(getattr(self, field), field)
        if self.resource_identifier != "*":
            _validate_identifier(self.resource_identifier, "resource_identifier")
        if (self.subject_identifier is None) == (self.role is None):
            raise ValueError("Exactly one grant subject or role is required.")
        if self.subject_identifier is not None:
            _validate_identifier(self.subject_identifier, "subject_identifier")
        if self.role is not None:
            _validate_identifier(self.role, "role")
        if tuple(sorted(set(self.allowed_actions), key=str)) != self.allowed_actions:
            raise ValueError("Grant actions must be unique and sorted.")
        for value in (self.valid_from, self.valid_until):
            if value is not None and value.tzinfo is None:
                raise ValueError("Grant validity must be timezone-aware.")
        if self.valid_from and self.valid_until and self.valid_from >= self.valid_until:
            raise ValueError("Grant validity interval is invalid.")
        return self


class AuditRecord(_FrozenContract):
    schema_version: Literal["cgr.pulsate-audit/1.0.0"] = AUDIT_CONTRACT_VERSION
    record_identifier: BoundedIdentifier
    event_time: datetime
    correlation_identifier: BoundedIdentifier
    request_identifier: BoundedIdentifier
    principal_audit_identifier: BoundedIdentifier
    tenant_audit_identifier: BoundedIdentifier
    action: ProtectedAction | Literal["authentication"]
    resource_type: BoundedName
    safe_resource_identity: BoundedIdentifier
    decision: Literal["allowed", "denied", "failed", "completed"]
    reason_category: BoundedName
    result_fingerprint: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None = None

    @model_validator(mode="after")
    def validate_record(self) -> AuditRecord:
        for field in (
            "record_identifier",
            "correlation_identifier",
            "request_identifier",
            "principal_audit_identifier",
            "tenant_audit_identifier",
            "resource_type",
            "safe_resource_identity",
            "reason_category",
        ):
            _validate_identifier(getattr(self, field), field)
        if self.event_time.tzinfo is None:
            raise ValueError("Audit time must be timezone-aware.")
        return self


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
