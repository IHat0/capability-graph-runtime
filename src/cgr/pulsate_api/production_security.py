"""Production JWT, durable authorization, and durable audit providers."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import stat
import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .production_configuration import PulsateRuntimeConfiguration
from .security import (
    AuditUnavailable,
    AuthenticationRejected,
    AuthenticationUnavailable,
    Authenticator,
    AuthorizationUnavailable,
    MetricsCollector,
    SecurityServices,
    StructuredEventLogger,
)
from .security_contracts import AuditRecord, AuthenticatedPrincipal, ResourceGrant

try:  # Declared and pinned by requirements/pulsate-ibm-runtime.lock.
    import jwt
except ImportError:  # pragma: no cover - exercised by dependency-boundary tests
    jwt = None


JWKS_DOCUMENT_MAXIMUM_BYTES = 256 * 1024
JWKS_MAXIMUM_KEYS = 32
AUDIT_GENESIS_FINGERPRINT = "0" * 64
GRANT_SCHEMA_VERSION = 1
AUDIT_SCHEMA_VERSION = 1
_DURABLE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")


class ProductionSecurityProviderError(RuntimeError):
    """Internal provider failure with a controlled, path-free message."""


def jwt_verifier_available() -> bool:
    return jwt is not None


def _safe_regular_file(path: Path, trusted_root: Path, *, maximum_bytes: int) -> bytes:
    try:
        root_metadata = trusted_root.lstat()
        if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
            raise OSError
        resolved_root = trusted_root.resolve(strict=True)
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise OSError
        if metadata.st_size > maximum_bytes:
            raise OSError
        resolved = path.resolve(strict=True)
        resolved.relative_to(resolved_root)
        with resolved.open("rb") as handle:
            payload = handle.read(maximum_bytes + 1)
        if len(payload) > maximum_bytes:
            raise OSError
        return payload
    except (OSError, ValueError):
        raise ProductionSecurityProviderError("Configured key material is unavailable or invalid.") from None


class OfflineJWKSAuthenticator:
    """PyJWT-backed verifier with a startup-loaded immutable key snapshot."""

    def __init__(
        self,
        configuration: PulsateRuntimeConfiguration,
        *,
        event_logger: StructuredEventLogger | None = None,
    ) -> None:
        self._configuration = configuration
        self._events = event_logger or StructuredEventLogger()
        self._snapshot: Mapping[str, tuple[str, Any]] = {}
        self._started = False
        self._lock = threading.RLock()

    def _load_snapshot(self) -> Mapping[str, tuple[str, Any]]:
        raw = _safe_regular_file(
            self._configuration.authentication.jwks_file,
            self._configuration.repositories.trusted_configuration_root,
            maximum_bytes=JWKS_DOCUMENT_MAXIMUM_BYTES,
        )
        try:
            document = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            raise ProductionSecurityProviderError("Configured key material is unavailable or invalid.") from None
        if not isinstance(document, dict) or set(document) != {"keys"}:
            raise ProductionSecurityProviderError("Configured key material is unavailable or invalid.")
        keys = document.get("keys")
        if not isinstance(keys, list) or not keys or len(keys) > JWKS_MAXIMUM_KEYS:
            raise ProductionSecurityProviderError("Configured key material is unavailable or invalid.")
        snapshot: dict[str, tuple[str, Any]] = {}
        validated: list[tuple[str, str, dict[str, Any]]] = []
        for entry in keys:
            if not isinstance(entry, dict):
                raise ProductionSecurityProviderError("Configured key material is unavailable or invalid.")
            kid = entry.get("kid")
            if not isinstance(kid, str) or not kid or len(kid) > 128 or kid in snapshot:
                raise ProductionSecurityProviderError("Configured key material is unavailable or invalid.")
            algorithm = entry.get("alg")
            if algorithm not in self._configuration.authentication.algorithms:
                raise ProductionSecurityProviderError("Configured key material is unavailable or invalid.")
            if entry.get("use", "sig") != "sig":
                raise ProductionSecurityProviderError("Configured key material is unavailable or invalid.")
            key_ops = entry.get("key_ops")
            if key_ops is not None and (not isinstance(key_ops, list) or "verify" not in key_ops):
                raise ProductionSecurityProviderError("Configured key material is unavailable or invalid.")
            key_type = entry.get("kty")
            if (algorithm.startswith("RS") and key_type != "RSA") or (
                algorithm.startswith("ES") and key_type != "EC"
            ):
                raise ProductionSecurityProviderError("Configured key material is unavailable or invalid.")
            expected_curve = {"ES256": "P-256", "ES384": "P-384", "ES512": "P-521"}.get(algorithm)
            if expected_curve is not None and entry.get("crv") != expected_curve:
                raise ProductionSecurityProviderError("Configured key material is unavailable or invalid.")
            snapshot[kid] = (algorithm, None)
            validated.append((kid, algorithm, entry))
        if jwt is None:
            raise AuthenticationUnavailable()
        snapshot = {}
        for kid, algorithm, entry in validated:
            try:
                key = jwt.PyJWK.from_dict(entry, algorithm=algorithm).key
            except Exception:
                raise ProductionSecurityProviderError("Configured key material is unavailable or invalid.") from None
            snapshot[kid] = (algorithm, key)
        return dict(sorted(snapshot.items()))

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._snapshot = self._load_snapshot()
            self._started = True

    def close(self) -> None:
        with self._lock:
            self._snapshot = {}
            self._started = False

    def ready(self) -> bool:
        with self._lock:
            return self._started and bool(self._snapshot)

    def reload(self) -> None:
        try:
            replacement = self._load_snapshot()
        except Exception:
            self._events.emit("authentication.keys_reload_failed")
            raise
        with self._lock:
            if not self._started:
                raise AuthenticationUnavailable()
            self._snapshot = replacement
        self._events.emit("authentication.keys_reloaded")

    def authenticate(self, bearer_token: str, *, now: datetime) -> AuthenticatedPrincipal:
        if not self.ready() or jwt is None:
            raise AuthenticationUnavailable()
        if len(bearer_token.encode("utf-8")) > self._configuration.authentication.maximum_token_bytes:
            raise AuthenticationRejected("oversized_credential")
        if bearer_token.count(".") != 2:
            raise AuthenticationRejected("malformed_credential")
        try:
            header = jwt.get_unverified_header(bearer_token)
            if not isinstance(header, dict):
                raise AuthenticationRejected()
            algorithm = header.get("alg")
            kid = header.get("kid")
            if algorithm not in self._configuration.authentication.algorithms or algorithm == "none":
                raise AuthenticationRejected("disallowed_algorithm")
            if not isinstance(kid, str) or not kid:
                raise AuthenticationRejected("unknown_key")
            with self._lock:
                selected = self._snapshot.get(kid)
            if selected is None or selected[0] != algorithm:
                raise AuthenticationRejected("unknown_key")
            auth = self._configuration.authentication
            claims = jwt.decode(
                bearer_token,
                selected[1],
                algorithms=[algorithm],
                audience=list(auth.audiences),
                issuer=auth.issuer,
                leeway=auth.clock_skew_seconds,
                options={
                    "require": ["exp", "iat", "iss", "aud", auth.subject_claim, auth.tenant_claim],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )
            subject = claims.get(auth.subject_claim)
            tenant = claims.get(auth.tenant_claim)
            if not isinstance(subject, str) or not subject or not isinstance(tenant, str) or not tenant:
                raise AuthenticationRejected("missing_identity")
            issued_at = claims.get("iat")
            if not isinstance(issued_at, (int, float)) or issued_at > now.timestamp() + auth.clock_skew_seconds:
                raise AuthenticationRejected("future_issued_at")
            scopes = _claim_values(claims.get(auth.scope_claim), split_spaces=True)
            roles = _claim_values(claims.get(auth.roles_claim), split_spaces=False)
            return AuthenticatedPrincipal(
                subject_identifier=subject,
                tenant_identifier=tenant,
                authentication_method="oidc-jwt",
                scopes=scopes,
                roles=roles,
                audit_identifier="principal-" + hashlib.sha256(
                    f"{tenant}\0{subject}".encode("utf-8")
                ).hexdigest()[:24],
            )
        except (AuthenticationRejected, AuthenticationUnavailable):
            raise
        except Exception:
            raise AuthenticationRejected() from None


def _claim_values(value: object, *, split_spaces: bool) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values = value.split() if split_spaces else (value,)
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        values = value
    else:
        raise AuthenticationRejected("invalid_claim")
    return tuple(sorted(set(values)))


def _open_database(
    path: Path,
    timeout_ms: int,
    *,
    trusted_root: Path | None = None,
) -> sqlite3.Connection:
    connection: sqlite3.Connection | None = None
    try:
        parent = path.parent
        parent.mkdir(parents=True, exist_ok=True)
        metadata = parent.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise OSError
        resolved_parent = parent.resolve(strict=True)
        if trusted_root is not None:
            trusted_metadata = trusted_root.lstat()
            if stat.S_ISLNK(trusted_metadata.st_mode) or not stat.S_ISDIR(trusted_metadata.st_mode):
                raise OSError
            resolved_parent.relative_to(trusted_root.resolve(strict=True))
        try:
            file_metadata = path.lstat()
        except FileNotFoundError:
            file_metadata = None
        if file_metadata is not None and (
            stat.S_ISLNK(file_metadata.st_mode) or not stat.S_ISREG(file_metadata.st_mode)
        ):
            raise OSError
        connection = sqlite3.connect(
            path,
            timeout=timeout_ms / 1000,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {timeout_ms}")
        connection.execute("PRAGMA journal_mode = WAL")
        opened_metadata = path.lstat()
        if stat.S_ISLNK(opened_metadata.st_mode) or not stat.S_ISREG(opened_metadata.st_mode):
            connection.close()
            raise OSError
        return connection
    except (OSError, ValueError, sqlite3.Error):
        if connection is not None:
            connection.close()
        raise ProductionSecurityProviderError("Durable security storage is unavailable.") from None


class SQLiteGrantProvider:
    def __init__(
        self,
        database_file: Path,
        *,
        busy_timeout_ms: int = 5000,
        allow_wildcards: bool = False,
        trusted_root: Path | None = None,
    ) -> None:
        self._path = database_file
        self._busy_timeout_ms = busy_timeout_ms
        self._allow_wildcards = allow_wildcards
        self._trusted_root = trusted_root
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    @property
    def allow_wildcards(self) -> bool:
        return self._allow_wildcards

    def start(self) -> None:
        with self._lock:
            if self._connection is not None:
                return
            connection = _open_database(
                self._path,
                self._busy_timeout_ms,
                trusted_root=self._trusted_root,
            )
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("CREATE TABLE IF NOT EXISTS schema_metadata (component TEXT PRIMARY KEY, version INTEGER NOT NULL)")
                existing = connection.execute("SELECT version FROM schema_metadata WHERE component='grants'").fetchone()
                if existing is not None and existing[0] != GRANT_SCHEMA_VERSION:
                    raise ProductionSecurityProviderError("Grant-store schema is incompatible.")
                connection.execute("INSERT OR IGNORE INTO schema_metadata(component, version) VALUES ('grants', ?)", (GRANT_SCHEMA_VERSION,))
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS resource_grants (
                    grant_identifier TEXT PRIMARY KEY, tenant_identifier TEXT NOT NULL,
                    subject_identifier TEXT, role TEXT, resource_type TEXT NOT NULL,
                    resource_identifier TEXT NOT NULL, allowed_actions_json TEXT NOT NULL,
                    valid_from TEXT, valid_until TEXT, created_at TEXT NOT NULL,
                    revoked_at TEXT, schema_version INTEGER NOT NULL,
                    CHECK ((subject_identifier IS NULL) != (role IS NULL)))"""
                )
                connection.execute("COMMIT")
            except Exception:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                connection.close()
                raise
            self._connection = connection

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def ready(self) -> bool:
        with self._lock:
            if self._connection is None:
                return False
            try:
                return self._connection.execute("SELECT 1").fetchone() == (1,)
            except sqlite3.Error:
                return False

    def create_grant(self, grant_identifier: str, grant: ResourceGrant, *, created_at: datetime | None = None) -> None:
        if not _DURABLE_IDENTIFIER.fullmatch(grant_identifier):
            raise ProductionSecurityProviderError("Grant identifier is invalid.")
        creation_time = created_at or datetime.now(timezone.utc)
        if creation_time.tzinfo is None:
            raise ProductionSecurityProviderError("Grant creation time is invalid.")
        connection = self._require_connection()
        values = (
            grant_identifier,
            grant.tenant_identifier,
            grant.subject_identifier,
            grant.role,
            grant.resource_type,
            grant.resource_identifier,
            json.dumps([str(action) for action in grant.allowed_actions], separators=(",", ":")),
            _iso(grant.valid_from),
            _iso(grant.valid_until),
            _iso(creation_time),
            None,
            GRANT_SCHEMA_VERSION,
        )
        with self._lock:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute("SELECT * FROM resource_grants WHERE grant_identifier=?", (grant_identifier,)).fetchone()
                if existing is None:
                    connection.execute("INSERT INTO resource_grants VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", values)
                elif (
                    tuple(existing[:9]) != values[:9]
                    or existing[10] is not None
                    or existing[11] != GRANT_SCHEMA_VERSION
                ):
                    raise ProductionSecurityProviderError("Grant identifier is already bound to different evidence.")
                connection.execute("COMMIT")
            except ProductionSecurityProviderError:
                connection.execute("ROLLBACK")
                raise
            except sqlite3.Error:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise AuthorizationUnavailable() from None

    def revoke(self, grant_identifier: str, *, revoked_at: datetime | None = None) -> None:
        if not _DURABLE_IDENTIFIER.fullmatch(grant_identifier):
            raise ProductionSecurityProviderError("Grant identifier is invalid.")
        revocation_time = revoked_at or datetime.now(timezone.utc)
        if revocation_time.tzinfo is None:
            raise ProductionSecurityProviderError("Grant revocation time is invalid.")
        connection = self._require_connection()
        with self._lock:
            try:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    "UPDATE resource_grants SET revoked_at=COALESCE(revoked_at, ?) WHERE grant_identifier=?",
                    (_iso(revocation_time), grant_identifier),
                )
                if cursor.rowcount != 1:
                    raise ProductionSecurityProviderError("Grant does not exist.")
                connection.execute("COMMIT")
            except ProductionSecurityProviderError:
                connection.execute("ROLLBACK")
                raise
            except sqlite3.Error:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise AuthorizationUnavailable() from None

    def grants_for(self, principal: AuthenticatedPrincipal) -> Sequence[ResourceGrant]:
        connection = self._require_connection()
        try:
            with self._lock:
                rows = connection.execute(
                    """SELECT tenant_identifier, subject_identifier, role, resource_type,
                    resource_identifier, allowed_actions_json, valid_from, valid_until
                    FROM resource_grants WHERE tenant_identifier=? AND revoked_at IS NULL
                    AND (subject_identifier=? OR role IN (%s)) ORDER BY grant_identifier"""
                    % (",".join("?" for _ in principal.roles) if principal.roles else "NULL"),
                    (principal.tenant_identifier, principal.subject_identifier, *principal.roles),
                ).fetchall()
            return tuple(_grant_from_row(row) for row in rows)
        except (sqlite3.Error, ValidationError, ValueError):
            raise AuthorizationUnavailable() from None

    def _require_connection(self) -> sqlite3.Connection:
        if not self.ready() or self._connection is None:
            raise AuthorizationUnavailable()
        return self._connection


def _grant_from_row(row: Sequence[object]) -> ResourceGrant:
    return ResourceGrant(
        tenant_identifier=row[0],
        subject_identifier=row[1],
        role=row[2],
        resource_type=row[3],
        resource_identifier=row[4],
        allowed_actions=tuple(json.loads(row[5])),
        valid_from=_datetime(row[6]),
        valid_until=_datetime(row[7]),
    )


class SQLiteAuditSink:
    def __init__(
        self,
        database_file: Path,
        *,
        busy_timeout_ms: int = 5000,
        verification_maximum_records: int = 100000,
        trusted_root: Path | None = None,
    ) -> None:
        self._path = database_file
        self._busy_timeout_ms = busy_timeout_ms
        self._verification_maximum_records = verification_maximum_records
        self._trusted_root = trusted_root
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def start(self) -> None:
        with self._lock:
            if self._connection is not None:
                return
            connection = _open_database(
                self._path,
                self._busy_timeout_ms,
                trusted_root=self._trusted_root,
            )
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("CREATE TABLE IF NOT EXISTS schema_metadata (component TEXT PRIMARY KEY, version INTEGER NOT NULL)")
                existing = connection.execute("SELECT version FROM schema_metadata WHERE component='audit'").fetchone()
                if existing is not None and existing[0] != AUDIT_SCHEMA_VERSION:
                    raise ProductionSecurityProviderError("Audit-store schema is incompatible.")
                connection.execute("INSERT OR IGNORE INTO schema_metadata(component, version) VALUES ('audit', ?)", (AUDIT_SCHEMA_VERSION,))
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS audit_records (
                    sequence_number INTEGER PRIMARY KEY, record_json TEXT NOT NULL UNIQUE,
                    previous_fingerprint TEXT NOT NULL, record_fingerprint TEXT NOT NULL UNIQUE,
                    schema_version INTEGER NOT NULL)"""
                )
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS audit_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), head_sequence INTEGER NOT NULL,
                    head_fingerprint TEXT NOT NULL, schema_version INTEGER NOT NULL)"""
                )
                connection.execute("INSERT OR IGNORE INTO audit_state VALUES (1, 0, ?, ?)", (AUDIT_GENESIS_FINGERPRINT, AUDIT_SCHEMA_VERSION))
                connection.execute("COMMIT")
            except Exception:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                connection.close()
                raise
            self._connection = connection
            if not self.verify_integrity():
                self.close()
                raise ProductionSecurityProviderError("Audit-store integrity verification failed.")

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def ready(self) -> bool:
        with self._lock:
            if self._connection is None:
                return False
            try:
                return self._connection.execute("SELECT 1").fetchone() == (1,)
            except sqlite3.Error:
                return False

    def append(self, record: AuditRecord) -> None:
        connection = self._require_connection()
        payload = record.canonical_json()
        with self._lock:
            try:
                connection.execute("BEGIN IMMEDIATE")
                head_sequence, previous = connection.execute(
                    "SELECT head_sequence, head_fingerprint FROM audit_state WHERE singleton=1"
                ).fetchone()
                sequence = head_sequence + 1
                fingerprint = _audit_fingerprint(sequence, previous, payload)
                connection.execute(
                    "INSERT INTO audit_records VALUES (?, ?, ?, ?, ?)",
                    (sequence, payload, previous, fingerprint, AUDIT_SCHEMA_VERSION),
                )
                connection.execute(
                    "UPDATE audit_state SET head_sequence=?, head_fingerprint=? WHERE singleton=1",
                    (sequence, fingerprint),
                )
                connection.execute("COMMIT")
            except sqlite3.Error:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise AuditUnavailable() from None

    def verify_integrity(self, maximum_records: int | None = None) -> bool:
        connection = self._connection
        if connection is None:
            return False
        limit = maximum_records or self._verification_maximum_records
        if limit < 1 or limit > self._verification_maximum_records:
            raise ValueError("Audit verification bound is invalid.")
        try:
            with self._lock:
                head = connection.execute("SELECT head_sequence, head_fingerprint FROM audit_state WHERE singleton=1").fetchone()
                rows = connection.execute(
                    "SELECT sequence_number, record_json, previous_fingerprint, record_fingerprint, schema_version FROM audit_records ORDER BY sequence_number LIMIT ?",
                    (limit + 1,),
                ).fetchall()
            if head is None or len(rows) > limit or head[0] != len(rows):
                return False
            previous = AUDIT_GENESIS_FINGERPRINT
            for expected, row in enumerate(rows, 1):
                sequence, payload, stored_previous, fingerprint, version = row
                if sequence != expected or stored_previous != previous or version != AUDIT_SCHEMA_VERSION:
                    return False
                if _audit_fingerprint(sequence, previous, payload) != fingerprint:
                    return False
                AuditRecord.model_validate_json(payload)
                previous = fingerprint
            return head == (len(rows), previous)
        except (sqlite3.Error, ValidationError, ValueError, TypeError):
            return False

    def _require_connection(self) -> sqlite3.Connection:
        if not self.ready() or self._connection is None:
            raise AuditUnavailable()
        return self._connection


def _audit_fingerprint(sequence: int, previous: str, payload: str) -> str:
    return hashlib.sha256(f"{sequence}\0{previous}\0{payload}".encode("utf-8")).hexdigest()


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value is not None else None


def _datetime(value: object) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) else None


class ProductionSecurityServices(SecurityServices):
    def __init__(
        self,
        configuration: PulsateRuntimeConfiguration,
        *,
        authenticator: Authenticator | None = None,
    ) -> None:
        if configuration.environment != "production":
            raise ValueError("Production security requires production configuration.")
        self.configuration = configuration
        self.configuration_valid = True
        super().__init__(
            authenticator=authenticator or OfflineJWKSAuthenticator(configuration),
            grant_provider=SQLiteGrantProvider(
                configuration.authorization.database_file,
                busy_timeout_ms=configuration.authorization.busy_timeout_ms,
                allow_wildcards=configuration.authorization.allow_wildcards,
                trusted_root=configuration.repositories.application_data_root,
            ),
            audit_sink=SQLiteAuditSink(
                configuration.audit.database_file,
                busy_timeout_ms=configuration.audit.busy_timeout_ms,
                verification_maximum_records=configuration.audit.verification_maximum_records,
                trusted_root=configuration.repositories.application_data_root,
            ),
            metrics=MetricsCollector(),
            event_logger=StructuredEventLogger(),
            read_audit_fail_closed=configuration.audit.read_fail_closed,
        )

    def start(self) -> None:
        self.events.emit(
            "production_security.starting",
            configuration_fingerprint=self.configuration.fingerprint,
        )
        super().start()

    def diagnostic_snapshot(self) -> Mapping[str, bool | str]:
        return {
            "configuration": self.configuration_valid,
            "authenticator": self.authenticator.ready(),
            "grant_store": self.grant_provider.ready(),
            "audit_store": self.audit_sink.ready(),
            "services": self.ready(),
            "configuration_fingerprint": self.configuration.fingerprint,
        }


def create_production_security_services(
    configuration: PulsateRuntimeConfiguration,
    *,
    authenticator_factory: Callable[[PulsateRuntimeConfiguration], Authenticator] | None = None,
) -> ProductionSecurityServices:
    authenticator = authenticator_factory(configuration) if authenticator_factory else None
    return ProductionSecurityServices(configuration, authenticator=authenticator)
