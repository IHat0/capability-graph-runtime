"""Injectable authentication, authorization, audit, and observability boundary."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import Counter
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Mapping, Protocol, Sequence

from .security_contracts import (
    AuditRecord,
    AuthenticatedPrincipal,
    ProtectedAction,
    RequestSecurityContext,
    ResourceGrant,
    utc_now,
)


CORRELATION_HEADER = "X-Correlation-ID"
_BEARER_PATTERN = re.compile(r"^Bearer ([^\s]{1,8192})$")
_CORRELATION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_RESOURCE_IDENTIFIER_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$"
)
_SAFE_EVENT_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
_SENSITIVE_FIELD = re.compile(
    r"authorization|credential|password|secret|token|api[_-]?key|payload|topology|structure",
    re.IGNORECASE,
)
_SECURITY_LOGGER = logging.getLogger("cgr.pulsate.security")


class SecurityBoundaryError(RuntimeError):
    def __init__(self, category: str, status_code: int, public_message: str) -> None:
        super().__init__(category)
        self.category = category
        self.status_code = status_code
        self.public_message = public_message


class AuthenticationRejected(SecurityBoundaryError):
    def __init__(self, category: str = "invalid_credential") -> None:
        super().__init__(category, 401, "Authentication is required.")


class AuthenticationUnavailable(SecurityBoundaryError):
    def __init__(self) -> None:
        super().__init__(
            "authentication_unavailable", 503, "Authentication service is unavailable."
        )


class AuthorizationDenied(SecurityBoundaryError):
    def __init__(self, category: str = "access_denied") -> None:
        super().__init__(category, 403, "Access to this resource is denied.")


class AuthorizationUnavailable(SecurityBoundaryError):
    def __init__(self) -> None:
        super().__init__(
            "authorization_unavailable", 503, "Authorization service is unavailable."
        )


class AuditUnavailable(SecurityBoundaryError):
    def __init__(self) -> None:
        super().__init__("audit_unavailable", 503, "Audit service is unavailable.")


class Authenticator(Protocol):
    def start(self) -> None:
        ...

    def close(self) -> None:
        ...

    def ready(self) -> bool:
        ...

    def authenticate(
        self, bearer_token: str, *, now: datetime
    ) -> AuthenticatedPrincipal:
        ...


class GrantProvider(Protocol):
    def start(self) -> None:
        ...

    def close(self) -> None:
        ...

    def ready(self) -> bool:
        ...

    def grants_for(
        self, principal: AuthenticatedPrincipal
    ) -> Sequence[ResourceGrant]:
        ...


class AuditSink(Protocol):
    def start(self) -> None:
        ...

    def close(self) -> None:
        ...

    def ready(self) -> bool:
        ...

    def append(self, record: AuditRecord) -> None:
        ...


class UnavailableAuthenticator:
    def start(self) -> None:
        pass

    def close(self) -> None:
        pass

    def ready(self) -> bool:
        return False

    def authenticate(self, bearer_token: str, *, now: datetime) -> AuthenticatedPrincipal:
        del bearer_token, now
        raise AuthenticationUnavailable()


class DevelopmentAuthenticator:
    def __init__(self, token: str, principal: AuthenticatedPrincipal) -> None:
        if not token or len(token.encode("utf-8")) > 8192:
            raise ValueError("Development credential configuration is invalid.")
        self._token = token
        self._principal = principal
        self._started = False

    def start(self) -> None:
        self._started = True
        _SECURITY_LOGGER.warning(
            json.dumps(
                {"event": "authentication.development_enabled"}, sort_keys=True
            )
        )

    def close(self) -> None:
        self._started = False

    def ready(self) -> bool:
        return self._started

    def authenticate(
        self, bearer_token: str, *, now: datetime
    ) -> AuthenticatedPrincipal:
        del now
        if not self._started:
            raise AuthenticationUnavailable()
        if not hmac.compare_digest(bearer_token, self._token):
            raise AuthenticationRejected()
        return self._principal


class StaticAuthenticator:
    """Deterministic injectable authenticator intended for tests."""

    def __init__(
        self,
        principals: Mapping[str, AuthenticatedPrincipal],
        *,
        startup_error: bool = False,
    ) -> None:
        self._principals = dict(principals)
        self._startup_error = startup_error
        self._started = False

    def start(self) -> None:
        if self._startup_error:
            raise AuthenticationUnavailable()
        self._started = True

    def close(self) -> None:
        self._started = False

    def ready(self) -> bool:
        return self._started

    def authenticate(
        self, bearer_token: str, *, now: datetime
    ) -> AuthenticatedPrincipal:
        del now
        if not self._started:
            raise AuthenticationUnavailable()
        principal = self._principals.get(bearer_token)
        if principal is None:
            raise AuthenticationRejected()
        return principal


class InMemoryGrantProvider:
    def __init__(
        self,
        grants: Sequence[ResourceGrant] = (),
        *,
        allow_wildcards: bool = False,
        failure: bool = False,
    ) -> None:
        self._grants = tuple(grants)
        self._allow_wildcards = allow_wildcards
        self._failure = failure
        self._started = False

    def start(self) -> None:
        self._started = True

    def close(self) -> None:
        self._started = False

    def ready(self) -> bool:
        return self._started and not self._failure

    def grants_for(
        self, principal: AuthenticatedPrincipal
    ) -> Sequence[ResourceGrant]:
        if not self.ready():
            raise AuthorizationUnavailable()
        return tuple(
            grant
            for grant in self._grants
            if grant.tenant_identifier == principal.tenant_identifier
        )

    @property
    def allow_wildcards(self) -> bool:
        return self._allow_wildcards


class InMemoryAuditSink:
    def __init__(self, *, failure: bool = False) -> None:
        self.records: list[AuditRecord] = []
        self._failure = failure
        self._started = False
        self._lock = threading.Lock()

    def start(self) -> None:
        self._started = True

    def close(self) -> None:
        self._started = False

    def ready(self) -> bool:
        return self._started and not self._failure

    def append(self, record: AuditRecord) -> None:
        if not self.ready():
            raise AuditUnavailable()
        with self._lock:
            self.records.append(record)


class UnavailableAuditSink(InMemoryAuditSink):
    def __init__(self) -> None:
        super().__init__(failure=True)


class MetricsCollector:
    """Dependency-free bounded-cardinality operational counters and durations."""

    _ALLOWED_LABELS = {"method", "route", "status", "category", "action"}

    def __init__(self) -> None:
        self._counters: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()
        self._durations: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()
        self._lock = threading.Lock()

    def increment(self, name: str, amount: int = 1, **labels: str) -> None:
        if amount < 0:
            raise ValueError("Metric increment cannot be negative.")
        bounded = self._labels(labels)
        with self._lock:
            self._counters[(name, bounded)] += amount

    def observe(self, name: str, value: float, **labels: str) -> None:
        bounded = self._labels(labels)
        with self._lock:
            self._durations[(name, bounded)] += max(0.0, value)

    def _labels(self, labels: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
        if not set(labels) <= self._ALLOWED_LABELS:
            raise ValueError("Metric label is not allowed.")
        values: list[tuple[str, str]] = []
        for key, value in sorted(labels.items()):
            if len(value) > 64 or not _SAFE_EVENT_VALUE.fullmatch(value):
                raise ValueError("Metric label value is invalid.")
            values.append((key, value))
        return tuple(values)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "counters": {
                    self._key(name, labels): value
                    for (name, labels), value in sorted(self._counters.items())
                },
                "durations_seconds": {
                    self._key(name, labels): value
                    for (name, labels), value in sorted(self._durations.items())
                },
            }

    @staticmethod
    def _key(name: str, labels: tuple[tuple[str, str], ...]) -> str:
        suffix = ",".join(f"{key}={value}" for key, value in labels)
        return f"{name}|{suffix}" if suffix else name


class StructuredEventLogger:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or _SECURITY_LOGGER

    def emit(self, event: str, **fields: object) -> None:
        safe: dict[str, object] = {"event": event, "timestamp": utc_now().isoformat()}
        for key, value in fields.items():
            if _SENSITIVE_FIELD.search(key):
                safe[key] = "redacted"
            elif value is None or isinstance(value, (bool, int, float)):
                safe[key] = value
            elif (
                isinstance(value, str)
                and len(value) <= 128
                and _SAFE_EVENT_VALUE.fullmatch(value)
            ):
                safe[key] = value
            else:
                safe[key] = "redacted"
        self._logger.info(json.dumps(safe, sort_keys=True, separators=(",", ":")))


@dataclass(frozen=True)
class RouteProtection:
    actions: tuple[ProtectedAction, ...]
    resource_type: str
    identifier_source: str
    audit_write: bool = False


PUBLIC_ROUTES = frozenset({
    ("GET", "/api/v1/health"),
    ("GET", "/live"),
    ("GET", "/ready"),
})

ROUTE_PROTECTIONS: dict[tuple[str, str], RouteProtection] = {
    ("POST", "/api/v1/scientific/artifacts"): RouteProtection(
        (ProtectedAction.EXECUTION_REQUEST,),
        "scientific-execution",
        "static:collection",
        True,
    ),
    ("POST", "/api/v1/scientific/objectives/compile"): RouteProtection(
        (ProtectedAction.PLANNING_EVALUATE,),
        "scientific-execution",
        "static:collection",
        True,
    ),
    (
        "GET",
        "/api/v1/scientific/executions/{execution_identifier}",
    ): RouteProtection(
        (ProtectedAction.RUN_READ,),
        "scientific-execution",
        "path:execution_identifier",
    ),
    (
        "POST",
        "/api/v1/scientific/executions/{execution_identifier}/execute",
    ): RouteProtection(
        (ProtectedAction.EXECUTION_REQUEST,),
        "scientific-execution",
        "path:execution_identifier",
        True,
    ),
    ("GET", "/api/v1/molecular/scenes/projected"): RouteProtection(
        (ProtectedAction.PROJECT_READ, ProtectedAction.SCENE_READ),
        "project",
        "query:project_identifier",
    ),
    ("GET", "/api/v1/molecular/scenes/native-structure"): RouteProtection(
        (ProtectedAction.PROJECT_READ, ProtectedAction.ARTIFACT_READ),
        "project",
        "query:project_identifier",
    ),
    ("GET", "/api/v1/molecular/scenes/topology"): RouteProtection(
        (ProtectedAction.PROJECT_READ, ProtectedAction.ARTIFACT_READ),
        "project",
        "query:project_identifier",
    ),
    (
        "POST",
        "/api/v1/molecular/projects/{project_identifier}/planning/evaluate",
    ): RouteProtection(
        (ProtectedAction.PROJECT_READ, ProtectedAction.PLANNING_EVALUATE),
        "project",
        "path:project_identifier",
        True,
    ),
    ("GET", "/api/v1/runs/capability"): RouteProtection(
        (ProtectedAction.RUN_READ,), "run", "static:capability"
    ),
    ("GET", "/api/v1/experiments/presets"): RouteProtection(
        (ProtectedAction.EXPERIMENT_READ,),
        "experiment",
        "static:preset-catalogue",
    ),
    (
        "GET",
        "/api/v1/experiments/presets/{preset_identifier}",
    ): RouteProtection(
        (ProtectedAction.EXPERIMENT_READ,),
        "experiment",
        "path:preset_identifier",
    ),
    (
        "GET",
        "/api/v1/experiments/presets/{preset_identifier}/scene",
    ): RouteProtection(
        (ProtectedAction.EXPERIMENT_READ, ProtectedAction.SCENE_READ),
        "experiment",
        "path:preset_identifier",
    ),
    ("POST", "/api/v1/experiments/plan"): RouteProtection(
        (ProtectedAction.PLANNING_EVALUATE,),
        "experiment",
        "static:planner",
        True,
    ),
    ("GET", "/api/v1/experiments/interpreter/capability"): RouteProtection(
        (ProtectedAction.EXPERIMENT_READ,),
        "experiment",
        "static:interpreter",
    ),
    ("POST", "/api/v1/experiments/interpret"): RouteProtection(
        (ProtectedAction.PLANNING_EVALUATE,),
        "experiment",
        "static:interpreter",
        True,
    ),
    (
        "POST",
        "/api/v1/experiments/{interpretation_identifier}/approve",
    ): RouteProtection(
        (ProtectedAction.EXECUTION_APPROVE,),
        "experiment",
        "path:interpretation_identifier",
        True,
    ),
    ("GET", "/api/v1/experiments/{experiment_identifier}"): RouteProtection(
        (ProtectedAction.EXPERIMENT_READ,),
        "experiment",
        "path:experiment_identifier",
    ),
    ("POST", "/api/v1/runs"): RouteProtection(
        (ProtectedAction.EXECUTION_REQUEST,), "run", "static:collection", True
    ),
    ("GET", "/api/v1/runs/{run_identifier}"): RouteProtection(
        (ProtectedAction.RUN_READ,), "run", "path:run_identifier"
    ),
    ("GET", "/api/v1/runs/{run_identifier}/scene"): RouteProtection(
        (ProtectedAction.RUN_READ, ProtectedAction.SCENE_READ),
        "run",
        "path:run_identifier",
    ),
    ("GET", "/api/v1/runs/{run_identifier}/results"): RouteProtection(
        (ProtectedAction.RUN_READ,), "run", "path:run_identifier"
    ),
    ("GET", "/api/v1/runs/{run_identifier}/verification"): RouteProtection(
        (ProtectedAction.RUN_READ,), "run", "path:run_identifier"
    ),
    ("GET", "/api/v1/runs/{run_identifier}/receipt"): RouteProtection(
        (ProtectedAction.RUN_READ,), "run", "path:run_identifier"
    ),
    ("POST", "/api/v1/workflows/compile"): RouteProtection(
        (ProtectedAction.WORKFLOW_CREATE,),
        "workflow",
        "static:collection",
        True,
    ),
    (
        "GET",
        "/api/v1/workflows/{graph_identifier}/versions/{graph_version}",
    ): RouteProtection(
        (ProtectedAction.WORKFLOW_READ,),
        "workflow",
        "path:graph_identifier",
    ),
    (
        "POST",
        "/api/v1/workflows/{graph_identifier}/versions/{graph_version}/validate",
    ): RouteProtection(
        (ProtectedAction.WORKFLOW_READ,),
        "workflow",
        "path:graph_identifier",
        True,
    ),
    ("POST", "/api/v1/workflow-runs"): RouteProtection(
        (ProtectedAction.WORKFLOW_EXECUTE,),
        "workflow-run",
        "static:collection",
        True,
    ),
    ("GET", "/api/v1/workflow-runs/{graph_run_identifier}"): RouteProtection(
        (ProtectedAction.WORKFLOW_READ,),
        "workflow-run",
        "path:graph_run_identifier",
    ),
    (
        "POST",
        "/api/v1/workflow-runs/{graph_run_identifier}/approvals/{approval_identifier}",
    ): RouteProtection(
        (ProtectedAction.WORKFLOW_APPROVE,),
        "workflow-run",
        "path:graph_run_identifier",
        True,
    ),
    (
        "POST",
        "/api/v1/workflow-runs/{graph_run_identifier}/resume",
    ): RouteProtection(
        (ProtectedAction.WORKFLOW_EXECUTE,),
        "workflow-run",
        "path:graph_run_identifier",
        True,
    ),
    (
        "POST",
        "/api/v1/workflow-runs/{graph_run_identifier}/cancel",
    ): RouteProtection(
        (ProtectedAction.WORKFLOW_CANCEL,),
        "workflow-run",
        "path:graph_run_identifier",
        True,
    ),
    (
        "GET",
        "/api/v1/workflow-runs/{graph_run_identifier}/nodes",
    ): RouteProtection(
        (ProtectedAction.WORKFLOW_READ,),
        "workflow-run",
        "path:graph_run_identifier",
    ),
    (
        "GET",
        "/api/v1/workflow-runs/{graph_run_identifier}/evidence",
    ): RouteProtection(
        (ProtectedAction.WORKFLOW_READ,),
        "workflow-run",
        "path:graph_run_identifier",
    ),
}


_SECURITY_CONTEXT: ContextVar[RequestSecurityContext | None] = ContextVar(
    "pulsate_security_context", default=None
)


def current_security_context() -> RequestSecurityContext | None:
    return _SECURITY_CONTEXT.get()


def safe_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def valid_or_generated_correlation(value: str | None) -> str:
    if value is not None and _CORRELATION_PATTERN.fullmatch(value):
        return value
    return f"correlation-{uuid.uuid4().hex}"


def parse_bearer_header(value: str | None) -> str:
    if value is None:
        raise AuthenticationRejected("missing_credential")
    match = _BEARER_PATTERN.fullmatch(value)
    if match is None:
        raise AuthenticationRejected("malformed_credential")
    return match.group(1)


class AuthorizationPolicy:
    def __init__(self, provider: GrantProvider) -> None:
        self.provider = provider

    def authorize(
        self,
        principal: AuthenticatedPrincipal,
        action: ProtectedAction,
        resource_type: str,
        resource_identifier: str,
        *,
        now: datetime,
    ) -> bool:
        if (
            action.value not in principal.scopes
            and ProtectedAction.ADMINISTRATION.value not in principal.scopes
        ):
            return False
        grants = self.provider.grants_for(principal)
        allow_wildcards = bool(getattr(self.provider, "allow_wildcards", False))
        for grant in grants:
            if grant.resource_type != resource_type or action not in grant.allowed_actions:
                continue
            if grant.resource_identifier != resource_identifier and not (
                allow_wildcards and grant.resource_identifier == "*"
            ):
                continue
            if grant.subject_identifier != principal.subject_identifier and grant.role not in principal.roles:
                continue
            if grant.valid_from and now < grant.valid_from:
                continue
            if grant.valid_until and now >= grant.valid_until:
                continue
            return True
        return False


class SecurityServices:
    def __init__(
        self,
        *,
        authenticator: Authenticator,
        grant_provider: GrantProvider,
        audit_sink: AuditSink,
        metrics: MetricsCollector | None = None,
        event_logger: StructuredEventLogger | None = None,
        read_audit_fail_closed: bool = False,
    ) -> None:
        self.authenticator = authenticator
        self.grant_provider = grant_provider
        self.authorization = AuthorizationPolicy(grant_provider)
        self.audit_sink = audit_sink
        self.metrics = metrics or MetricsCollector()
        self.events = event_logger or StructuredEventLogger()
        self.read_audit_fail_closed = read_audit_fail_closed
        self._started = False

    @classmethod
    def from_environment(cls) -> SecurityServices:
        configured_environment = os.environ.get("PULSATE_ENVIRONMENT")
        environment = (
            configured_environment.strip().lower()
            if configured_environment is not None
            else "unconfigured"
        )
        if environment not in {"unconfigured", "test", "development", "production"}:
            raise ValueError("Pulsate environment configuration is invalid.")
        enabled = (
            os.environ.get("PULSATE_DEVELOPMENT_AUTH_ENABLED", "false")
            .strip()
            .lower()
            == "true"
        )
        if enabled and environment != "development":
            raise ValueError("Development authentication is forbidden outside development.")
        if enabled:
            token = os.environ.get("PULSATE_DEVELOPMENT_AUTH_TOKEN", "")
            scopes = tuple(
                sorted(
                    set(
                        filter(
                            None,
                            os.environ.get(
                                "PULSATE_DEVELOPMENT_AUTH_SCOPES", ""
                            ).split(","),
                        )
                    )
                )
            )
            principal = AuthenticatedPrincipal(
                subject_identifier=os.environ.get(
                    "PULSATE_DEVELOPMENT_SUBJECT", "development-user"
                ),
                tenant_identifier=os.environ.get(
                    "PULSATE_DEVELOPMENT_TENANT", "development-tenant"
                ),
                authentication_method="development-bearer",
                scopes=scopes,
                roles=(),
                audit_identifier=os.environ.get(
                    "PULSATE_DEVELOPMENT_AUDIT_ID", "development-user"
                ),
            )
            actions = tuple(sorted(ProtectedAction, key=str))
            development_actions = tuple(
                action
                for action in actions
                if action != ProtectedAction.ADMINISTRATION
            )
            grants = [
                ResourceGrant(
                    tenant_identifier=principal.tenant_identifier,
                    subject_identifier=principal.subject_identifier,
                    resource_type=kind,
                    resource_identifier="*",
                    allowed_actions=development_actions,
                )
                for kind in (
                    "project",
                    "run",
                    "experiment",
                    "scientific-execution",
                )
            ]
            return cls(
                authenticator=DevelopmentAuthenticator(token, principal),
                grant_provider=InMemoryGrantProvider(grants, allow_wildcards=True),
                audit_sink=InMemoryAuditSink(),
            )
        if environment == "production":
            from .production_configuration import (
                EnvironmentConfigurationSource,
                load_production_configuration,
            )
            from .production_security import create_production_security_services

            configuration = load_production_configuration(
                EnvironmentConfigurationSource()
            )
            return create_production_security_services(configuration)
        return cls(
            authenticator=UnavailableAuthenticator(),
            grant_provider=InMemoryGrantProvider(),
            audit_sink=UnavailableAuditSink(),
            read_audit_fail_closed=True,
        )

    def start(self) -> None:
        if self._started:
            return
        self.events.emit("application.startup")
        started: list[object] = []
        try:
            self.authenticator.start()
            started.append(self.authenticator)
            self.grant_provider.start()
            started.append(self.grant_provider)
            self.audit_sink.start()
            started.append(self.audit_sink)
            self._started = True
        except Exception:
            self._started = False
            for service in reversed(started):
                try:
                    service.close()  # type: ignore[attr-defined]
                except Exception:
                    pass
            raise

    def close(self) -> None:
        try:
            self.audit_sink.close()
        finally:
            try:
                self.grant_provider.close()
            finally:
                self.authenticator.close()
                self._started = False
                self.events.emit("application.shutdown")

    def ready(self) -> bool:
        return (
            self._started
            and self.authenticator.ready()
            and self.grant_provider.ready()
            and self.audit_sink.ready()
        )

    def diagnostic_snapshot(self) -> Mapping[str, bool]:
        """Internal coarse readiness state; never exposed as an unrestricted route."""

        return {
            "authenticator": self.authenticator.ready(),
            "grant_store": self.grant_provider.ready(),
            "audit_store": self.audit_sink.ready(),
            "services": self.ready(),
        }

    def authenticate(
        self,
        authorization: str | None,
        correlation_identifier: str,
        request_identifier: str,
    ) -> RequestSecurityContext:
        try:
            token = parse_bearer_header(authorization)
            principal = self.authenticator.authenticate(token, now=utc_now())
            context = RequestSecurityContext(
                principal=principal,
                correlation_identifier=correlation_identifier,
                request_identifier=request_identifier,
                authenticated_at=utc_now(),
            )
            _SECURITY_CONTEXT.set(context)
            self.events.emit(
                "authentication.succeeded",
                correlation_identifier=correlation_identifier,
                request_identifier=request_identifier,
                principal=principal.audit_identifier,
                tenant=safe_digest(principal.tenant_identifier),
            )
            return context
        except SecurityBoundaryError as exc:
            self.metrics.increment("authentication_failures", category=exc.category)
            self.events.emit(
                "authentication.failed",
                correlation_identifier=correlation_identifier,
                request_identifier=request_identifier,
                category=exc.category,
            )
            self._audit_authentication_failure(
                correlation_identifier, request_identifier, exc.category
            )
            raise

    def authorize(
        self,
        context: RequestSecurityContext,
        protection: RouteProtection,
        resource_identifier: str,
    ) -> None:
        for action in protection.actions:
            try:
                allowed = self.authorization.authorize(
                    context.principal,
                    action,
                    protection.resource_type,
                    resource_identifier,
                    now=utc_now(),
                )
            except SecurityBoundaryError:
                raise
            except Exception:
                raise AuthorizationUnavailable() from None
            if not allowed:
                self.metrics.increment("authorization_denials", action=action.value)
                self.events.emit(
                    "authorization.denied",
                    correlation_identifier=context.correlation_identifier,
                    request_identifier=context.request_identifier,
                    principal=context.principal.audit_identifier,
                    action=action.value,
                    resource_type=protection.resource_type,
                    resource=safe_digest(resource_identifier),
                )
                self._audit(
                    context,
                    action,
                    protection.resource_type,
                    resource_identifier,
                    "denied",
                    "access_denied",
                    mandatory=True,
                )
                raise AuthorizationDenied()
            self.events.emit(
                "authorization.allowed",
                correlation_identifier=context.correlation_identifier,
                request_identifier=context.request_identifier,
                principal=context.principal.audit_identifier,
                action=action.value,
                resource_type=protection.resource_type,
                resource=safe_digest(resource_identifier),
            )
            self._audit(
                context,
                action,
                protection.resource_type,
                resource_identifier,
                "allowed",
                "policy_allowed",
                mandatory=(
                    protection.audit_write or self.read_audit_fail_closed
                ),
            )

    def audit_invalid_resource_denial(
        self,
        context: RequestSecurityContext,
        protection: RouteProtection,
    ) -> None:
        action = protection.actions[0]
        self.metrics.increment("authorization_denials", action=action.value)
        self.events.emit(
            "authorization.denied",
            correlation_identifier=context.correlation_identifier,
            request_identifier=context.request_identifier,
            principal=context.principal.audit_identifier,
            action=action.value,
            resource_type=protection.resource_type,
            resource="unresolved-resource",
        )
        self._audit(
            context,
            action,
            protection.resource_type,
            "unresolved-resource",
            "denied",
            "invalid_resource_identity",
            mandatory=True,
        )

    def audit_planning_result(
        self, result_fingerprint: str, *, completed: bool = True
    ) -> None:
        context = current_security_context()
        if context is None:
            return
        self._audit(
            context,
            ProtectedAction.PLANNING_EVALUATE,
            "project",
            "planning-result",
            "completed" if completed else "failed",
            "planning_completed" if completed else "planning_failed",
            mandatory=True,
            result_fingerprint=result_fingerprint,
        )

    def _audit_authentication_failure(
        self, correlation: str, request: str, category: str
    ) -> None:
        record = AuditRecord(
            record_identifier=f"audit-{uuid.uuid4().hex}",
            event_time=utc_now(),
            correlation_identifier=correlation,
            request_identifier=request,
            principal_audit_identifier="unauthenticated",
            tenant_audit_identifier="unknown-tenant",
            action="authentication",
            resource_type="authentication",
            safe_resource_identity="authentication-boundary",
            decision="denied",
            reason_category=category,
        )
        try:
            self.audit_sink.append(record)
        except Exception:
            pass

    def _audit(
        self,
        context: RequestSecurityContext,
        action: ProtectedAction,
        resource_type: str,
        resource_identifier: str,
        decision: Literal["allowed", "denied", "failed", "completed"],
        reason: str,
        *,
        mandatory: bool,
        result_fingerprint: str | None = None,
    ) -> None:
        record = AuditRecord(
            record_identifier=f"audit-{uuid.uuid4().hex}",
            event_time=utc_now(),
            correlation_identifier=context.correlation_identifier,
            request_identifier=context.request_identifier,
            principal_audit_identifier=context.principal.audit_identifier,
            tenant_audit_identifier=safe_digest(
                context.principal.tenant_identifier
            ),
            action=action,
            resource_type=resource_type,
            safe_resource_identity=safe_digest(resource_identifier),
            decision=decision,
            reason_category=reason,
            result_fingerprint=result_fingerprint,
        )
        try:
            self.audit_sink.append(record)
        except Exception:
            if mandatory:
                raise AuditUnavailable() from None

    def clear_context(self) -> None:
        _SECURITY_CONTEXT.set(None)


def route_resource_identifier(
    protection: RouteProtection,
    *,
    path_params: Mapping[str, str],
    query_params: Mapping[str, str],
) -> str:
    source, value = protection.identifier_source.split(":", 1)
    if source == "static":
        return value
    candidate = path_params.get(value) if source == "path" else query_params.get(value)
    if candidate is None or not _RESOURCE_IDENTIFIER_PATTERN.fullmatch(candidate):
        raise AuthorizationDenied()
    return candidate


def request_identifier() -> str:
    return f"request-{uuid.uuid4().hex}"


def monotonic_time() -> float:
    return time.monotonic()
