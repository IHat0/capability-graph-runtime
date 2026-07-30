"""Read-only native molecular scene service for the Pulsate API."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote, urlencode

from cgr.molecular import (
    MolecularArtifactRepository,
    MolecularArtifactRepositoryError,
    MolecularProject,
    MolecularSceneProjection,
    MolecularSceneProjectionError,
    MolecularSceneProjectionIntegrityError,
    MolecularSceneProjectionNotFoundError,
    ProjectedMolecularStructure,
    project_molecular_scene,
    read_projected_structure_bytes,
)
from cgr.science import ArtifactReference

_NATIVE_STRUCTURE_ROUTE = "/api/v1/molecular/scenes/native-structure"
_TOPOLOGY_ROUTE = "/api/v1/molecular/scenes/topology"


class NativeMolecularSceneError(ValueError):
    """Base error for controlled native molecular scene service failures."""


class NativeMolecularSceneNotFoundError(NativeMolecularSceneError):
    """Raised when a requested project, scene, or structure is absent."""


class NativeMolecularSceneIntegrityError(NativeMolecularSceneError):
    """Raised when native molecular scene evidence is inconsistent."""


class NativeMolecularSceneUnavailableError(NativeMolecularSceneError):
    """Raised when the native molecular scene service is unavailable."""


@dataclass(frozen=True, slots=True)
class NativeMolecularResource:
    """Exact native bytes and their reduced public artifact identity."""

    payload: bytes
    media_type: str
    content_sha256: str
    byte_size: int
    artifact_identifier: str


class NativeMolecularSceneService:
    """Resolve immutable projects through one owned artifact repository."""

    def __init__(
        self,
        *,
        project_resolver: Callable[[str], MolecularProject | None],
        repository: MolecularArtifactRepository,
    ) -> None:
        self._project_resolver = project_resolver
        self._repository = repository
        self._lock = threading.RLock()
        self._started = False

    def start(self) -> None:
        """Start the owned artifact repository idempotently."""

        with self._lock:
            if self._started:
                return
            try:
                self._repository.start()
            except MolecularArtifactRepositoryError:
                raise NativeMolecularSceneUnavailableError(
                    "Native molecular scene service could not start."
                ) from None
            self._started = True

    def close(self) -> None:
        """Stop the service and its repository idempotently."""

        with self._lock:
            self._started = False
            self._repository.close()

    def describe_scene(
        self,
        *,
        project_identifier: str,
        scene_identifier: str,
    ) -> dict[str, object]:
        """Return deterministic metadata without source bytes or topology arrays."""

        with self._lock:
            projection = self._resolve_projection(
                project_identifier=project_identifier,
                scene_identifier=scene_identifier,
            )
            return {
                "schema_version": projection.scene.schema_version.model_dump(
                    mode="json"
                ),
                "project_identifier": projection.project_identifier,
                "scene_identifier": projection.scene.scene_identifier,
                "primary_structure_identifier": (
                    projection.primary_structure_identifier
                ),
                "structures": [
                    self._structure_metadata(
                        projected=projected,
                        project_identifier=project_identifier,
                        scene_identifier=scene_identifier,
                    )
                    for projected in projection.structures
                ],
                "components": [
                    component.model_dump(mode="json")
                    for component in projection.components
                ],
                "selections": [
                    selection.model_dump(mode="json")
                    for selection in projection.selections
                ],
                "regions": [
                    region.model_dump(mode="json")
                    for region in projection.regions
                ],
                "artifact_references": [
                    self._public_artifact_identity(reference)
                    for reference in projection.artifact_references
                ],
            }

    def read_native_structure(
        self,
        *,
        project_identifier: str,
        scene_identifier: str,
        structure_identifier: str,
    ) -> NativeMolecularResource:
        """Return exact native structure bytes from the projected source reference."""

        with self._lock:
            projection = self._resolve_projection(
                project_identifier=project_identifier,
                scene_identifier=scene_identifier,
            )
            projected = self._resolve_projected_structure(
                projection,
                structure_identifier,
            )
            try:
                payload = read_projected_structure_bytes(
                    projection=projection,
                    structure_identifier=structure_identifier,
                    repository=self._repository,
                )
            except MolecularSceneProjectionNotFoundError:
                raise NativeMolecularSceneNotFoundError(
                    "Requested molecular structure was not found."
                ) from None
            except MolecularSceneProjectionIntegrityError:
                raise NativeMolecularSceneIntegrityError(
                    "Native molecular structure evidence is invalid."
                ) from None
            except MolecularSceneProjectionError:
                raise NativeMolecularSceneUnavailableError(
                    "Native molecular structure service is unavailable."
                ) from None
            return self._resource(
                payload=payload,
                reference=projected.source_artifact,
            )

    def read_topology(
        self,
        *,
        project_identifier: str,
        scene_identifier: str,
        structure_identifier: str,
    ) -> NativeMolecularResource:
        """Return exact canonical topology bytes from validated projection metadata."""

        with self._lock:
            projection = self._resolve_projection(
                project_identifier=project_identifier,
                scene_identifier=scene_identifier,
            )
            projected = self._resolve_projected_structure(
                projection,
                structure_identifier,
            )
            payload = projected.topology.to_canonical_json().encode("utf-8")
            return self._resource(
                payload=payload,
                reference=projected.topology_artifact,
            )

    def _require_started(self) -> None:
        if not self._started:
            raise NativeMolecularSceneUnavailableError(
                "Native molecular scene service is unavailable."
            )

    def _resolve_projection(
        self,
        *,
        project_identifier: str,
        scene_identifier: str,
    ) -> MolecularSceneProjection:
        self._require_started()
        try:
            project = self._project_resolver(project_identifier)
        except Exception:
            raise NativeMolecularSceneUnavailableError(
                "Native molecular project resolution is unavailable."
            ) from None
        if project is None:
            raise NativeMolecularSceneNotFoundError(
                "Requested molecular project was not found."
            )
        if (
            not isinstance(project, MolecularProject)
            or project.project_identifier != project_identifier
        ):
            raise NativeMolecularSceneIntegrityError(
                "Resolved molecular project identity is inconsistent."
            )
        try:
            return project_molecular_scene(
                project=project,
                scene_identifier=scene_identifier,
                repository=self._repository,
            )
        except MolecularSceneProjectionNotFoundError:
            raise NativeMolecularSceneNotFoundError(
                "Requested molecular scene was not found."
            ) from None
        except MolecularSceneProjectionIntegrityError:
            raise NativeMolecularSceneIntegrityError(
                "Native molecular scene evidence is invalid."
            ) from None
        except MolecularSceneProjectionError:
            raise NativeMolecularSceneUnavailableError(
                "Native molecular scene projection is unavailable."
            ) from None

    @staticmethod
    def _resolve_projected_structure(
        projection: MolecularSceneProjection,
        structure_identifier: str,
    ) -> ProjectedMolecularStructure:
        for projected in projection.structures:
            if projected.structure_identifier == structure_identifier:
                return projected
        raise NativeMolecularSceneNotFoundError(
            "Requested molecular structure was not found."
        )

    @classmethod
    def _structure_metadata(
        cls,
        *,
        projected: ProjectedMolecularStructure,
        project_identifier: str,
        scene_identifier: str,
    ) -> dict[str, object]:
        structure = projected.structure
        query = (
            ("project_identifier", project_identifier),
            ("scene_identifier", scene_identifier),
            ("structure_identifier", structure.structure_identifier),
        )
        return {
            "structure_identifier": structure.structure_identifier,
            "system_identifier": structure.system_identifier,
            "native_format": projected.native_format,
            "coordinate_unit": projected.coordinate_unit,
            "atom_count": structure.atom_count,
            "bond_count": structure.bond_count,
            "residue_count": structure.residue_count,
            "chain_count": structure.chain_count,
            "model_count": structure.model_count,
            "frame_count": structure.frame_count,
            "source_artifact": cls._public_artifact_identity(
                projected.source_artifact
            ),
            "topology_artifact": cls._public_artifact_identity(
                projected.topology_artifact
            ),
            "native_structure_url": cls._resource_url(
                _NATIVE_STRUCTURE_ROUTE,
                query,
            ),
            "topology_url": cls._resource_url(_TOPOLOGY_ROUTE, query),
        }

    @staticmethod
    def _public_artifact_identity(
        reference: ArtifactReference,
    ) -> dict[str, object]:
        if reference.byte_size is None:
            raise NativeMolecularSceneIntegrityError(
                "A molecular artifact has no declared byte size."
            )
        return {
            "schema_version": reference.schema_version.model_dump(mode="json"),
            "artifact_identifier": reference.artifact_identifier,
            "artifact_type": reference.artifact_type,
            "media_type": reference.media_type,
            "content_sha256": reference.content_sha256,
            "byte_size": reference.byte_size,
        }

    @staticmethod
    def _resource_url(
        route: str,
        query: tuple[tuple[str, str], ...],
    ) -> str:
        return f"{route}?{urlencode(query, safe='', quote_via=quote)}"

    @staticmethod
    def _resource(
        *,
        payload: bytes,
        reference: ArtifactReference,
    ) -> NativeMolecularResource:
        if reference.byte_size is None:
            raise NativeMolecularSceneIntegrityError(
                "A molecular resource has no declared byte size."
            )
        if len(payload) != reference.byte_size:
            raise NativeMolecularSceneIntegrityError(
                "Molecular resource byte size is inconsistent."
            )
        if hashlib.sha256(payload).hexdigest() != reference.content_sha256:
            raise NativeMolecularSceneIntegrityError(
                "Molecular resource content identity is inconsistent."
            )
        return NativeMolecularResource(
            payload=payload,
            media_type=reference.media_type,
            content_sha256=reference.content_sha256,
            byte_size=reference.byte_size,
            artifact_identifier=reference.artifact_identifier,
        )
