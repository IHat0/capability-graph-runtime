"""Trusted local production capability-catalogue loader."""

from __future__ import annotations

import json
import os
import stat
import threading
from collections.abc import Iterable
from pathlib import Path

from pydantic import ValidationError

from cgr.kernel.contracts import CapabilityVersion
from cgr.science import (
    CapabilityAvailabilityClassification,
    CapabilityExecutionEnvelope,
    ProductionCapabilityCatalogueDocument,
    ScientificCapabilityCatalog,
    ScientificCapabilityCatalogError,
)

from .production_configuration import CapabilityCatalogueConfiguration
from .security import StructuredEventLogger


class ProductionCatalogueError(ScientificCapabilityCatalogError):
    """Controlled path-free production catalogue failure."""


def _is_link_or_junction(path: Path, mode: int) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return stat.S_ISLNK(mode) or bool(is_junction and is_junction())


def _read_trusted_catalogue_file(
    path: Path,
    trusted_root: Path,
    *,
    maximum_bytes: int,
) -> bytes:
    try:
        root_metadata = trusted_root.lstat()
        if _is_link_or_junction(trusted_root, root_metadata.st_mode) or not stat.S_ISDIR(
            root_metadata.st_mode
        ):
            raise OSError
        path_metadata = path.lstat()
        if _is_link_or_junction(path, path_metadata.st_mode) or not stat.S_ISREG(
            path_metadata.st_mode
        ):
            raise OSError
        resolved_root = trusted_root.resolve(strict=True)
        relative = path.relative_to(trusted_root)
        current = trusted_root
        for index, part in enumerate(relative.parts):
            current = current / part
            metadata = current.lstat()
            if _is_link_or_junction(current, metadata.st_mode):
                raise OSError
            is_last = index == len(relative.parts) - 1
            if is_last and not stat.S_ISREG(metadata.st_mode):
                raise OSError
            if not is_last and not stat.S_ISDIR(metadata.st_mode):
                raise OSError
        resolved_path = path.resolve(strict=True)
        resolved_path.relative_to(resolved_root)
        if path_metadata.st_size > maximum_bytes:
            raise OSError
        with path.open("rb") as handle:
            opened_metadata = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(opened_metadata.st_mode)
                or opened_metadata.st_size > maximum_bytes
                or (opened_metadata.st_dev, opened_metadata.st_ino)
                != (path_metadata.st_dev, path_metadata.st_ino)
            ):
                raise OSError
            payload = handle.read(maximum_bytes + 1)
        if len(payload) > maximum_bytes:
            raise OSError
        return payload
    except (OSError, ValueError):
        raise ProductionCatalogueError(
            "Production capability catalogue is unavailable or invalid."
        ) from None


class ProductionCapabilityCatalogue(ScientificCapabilityCatalog):
    """Immutable request-time snapshot loaded from one trusted local document."""

    def __init__(
        self,
        configuration: CapabilityCatalogueConfiguration,
        *,
        trusted_configuration_root: Path,
        event_logger: StructuredEventLogger | None = None,
    ) -> None:
        self._configuration = configuration
        self._trusted_root = trusted_configuration_root
        self._events = event_logger or StructuredEventLogger()
        self._snapshot = ScientificCapabilityCatalog()
        self._document: ProductionCapabilityCatalogueDocument | None = None
        self._started = False
        self._lock = threading.RLock()

    def _validated_replacement(
        self,
    ) -> tuple[
        ScientificCapabilityCatalog,
        ProductionCapabilityCatalogueDocument | None,
    ]:
        if not self._configuration.enabled:
            if not self._configuration.allow_empty:
                raise ProductionCatalogueError(
                    "Production capability catalogue is unavailable or invalid."
                )
            return ScientificCapabilityCatalog(), None
        path = self._configuration.catalogue_file
        if path is None:
            raise ProductionCatalogueError(
                "Production capability catalogue is unavailable or invalid."
            )
        payload = _read_trusted_catalogue_file(
            path,
            self._trusted_root,
            maximum_bytes=self._configuration.maximum_bytes,
        )
        try:
            raw_document = json.loads(payload)
            if not isinstance(raw_document, dict):
                raise ValueError
            entries = raw_document.get("entries")
            if not isinstance(entries, list):
                raise ValueError
            if len(entries) > self._configuration.maximum_entries:
                raise ValueError
            document = ProductionCapabilityCatalogueDocument.model_validate(raw_document)
            if payload != document.to_document_json().encode("utf-8"):
                raise ValueError
            if (
                document.catalogue_identifier
                != self._configuration.expected_identifier
                or document.catalogue_version
                != self._configuration.expected_version
                or document.catalogue_fingerprint
                != self._configuration.expected_fingerprint
            ):
                raise ValueError
            if not document.entries and not self._configuration.allow_empty:
                raise ValueError
            selected = tuple(
                entry.envelope
                for entry in document.entries
                if entry.availability
                in {
                    CapabilityAvailabilityClassification.AVAILABLE,
                    CapabilityAvailabilityClassification.CONDITIONAL,
                }
            )
            return ScientificCapabilityCatalog(selected), document
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            RecursionError,
            ValidationError,
            ValueError,
            TypeError,
            ScientificCapabilityCatalogError,
        ):
            raise ProductionCatalogueError(
                "Production capability catalogue is unavailable or invalid."
            ) from None

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            snapshot, document = self._validated_replacement()
            self._snapshot = snapshot
            self._document = document
            self._started = True
        self._emit("capability_catalogue.loaded", document)

    def close(self) -> None:
        with self._lock:
            self._snapshot = ScientificCapabilityCatalog()
            self._document = None
            self._started = False

    def ready(self) -> bool:
        with self._lock:
            return self._started

    def reload(self) -> None:
        try:
            snapshot, document = self._validated_replacement()
        except Exception:
            self._events.emit("capability_catalogue.reload_failed")
            raise
        with self._lock:
            if not self._started:
                raise ProductionCatalogueError(
                    "Production capability catalogue is unavailable or invalid."
                )
            self._snapshot = snapshot
            self._document = document
        self._emit("capability_catalogue.reloaded", document)

    @property
    def document(self) -> ProductionCapabilityCatalogueDocument | None:
        with self._lock:
            return self._document

    def diagnostic_snapshot(self) -> dict[str, str | int | bool | None]:
        with self._lock:
            document = self._document
            return {
                "ready": self._started,
                "enabled": self._configuration.enabled,
                "catalogue_identifier": (
                    document.catalogue_identifier if document is not None else None
                ),
                "catalogue_version": (
                    str(document.catalogue_version) if document is not None else None
                ),
                "catalogue_fingerprint": (
                    document.catalogue_fingerprint if document is not None else None
                ),
                "entry_count": len(document.entries) if document is not None else 0,
            }

    def envelopes(self) -> tuple[CapabilityExecutionEnvelope, ...]:
        return self._require_snapshot().envelopes()

    def capability_names(self) -> tuple[str, ...]:
        return self._require_snapshot().capability_names()

    def get(
        self,
        capability_name: str,
        version: CapabilityVersion,
    ) -> CapabilityExecutionEnvelope:
        return self._require_snapshot().get(capability_name, version)

    def find(
        self,
        *,
        objective_type: str | None = None,
        input_artifact_types: tuple[str, ...] = (),
        execution_target: str | None = None,
    ) -> tuple[CapabilityExecutionEnvelope, ...]:
        return self._require_snapshot().find(
            objective_type=objective_type,
            input_artifact_types=input_artifact_types,
            execution_target=execution_target,
        )

    def register(self, envelope: CapabilityExecutionEnvelope) -> None:
        del envelope
        raise ProductionCatalogueError("Production capability catalogue is immutable.")

    def register_many(self, envelopes: Iterable[CapabilityExecutionEnvelope]) -> None:
        del envelopes
        raise ProductionCatalogueError("Production capability catalogue is immutable.")

    def _require_snapshot(self) -> ScientificCapabilityCatalog:
        with self._lock:
            if not self._started:
                raise ProductionCatalogueError(
                    "Production capability catalogue is unavailable or invalid."
                )
            return self._snapshot

    def _emit(
        self,
        event: str,
        document: ProductionCapabilityCatalogueDocument | None,
    ) -> None:
        self._events.emit(
            event,
            catalogue=(
                document.catalogue_identifier if document is not None else "explicit-empty"
            ),
            fingerprint=(
                document.catalogue_fingerprint if document is not None else "explicit-empty"
            ),
            entry_count=len(document.entries) if document is not None else 0,
        )
