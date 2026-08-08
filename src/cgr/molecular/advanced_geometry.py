"""Engine-neutral contracts for advanced molecular geometry operations."""

from __future__ import annotations

import math
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion
from cgr.science import CanonicalModel
from cgr.science.canonical import validate_identifier


_SCHEMA_VERSION = CapabilityVersion(major=1, minor=0, patch=0)


def _finite(value: float, *, label: str) -> float:
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{label} must be finite.")
    return normalized


class MolecularSubstructureMatch(CanonicalModel):
    """One deterministic SMARTS/substructure match against a molecular graph."""

    match_index: int = Field(ge=0)
    atom_indices: tuple[int, ...] = Field(min_length=1)
    bond_indices: tuple[int, ...] = ()

    @field_validator("atom_indices")
    @classmethod
    def validate_atom_indices(
        cls,
        value: tuple[int, ...],
    ) -> tuple[int, ...]:
        if any(index < 0 for index in value):
            raise ValueError("Substructure atom indices cannot be negative.")
        if len(value) != len(set(value)):
            raise ValueError("Substructure atom indices must be unique.")
        return value

    @field_validator("bond_indices")
    @classmethod
    def validate_bond_indices(
        cls,
        value: tuple[int, ...],
    ) -> tuple[int, ...]:
        if any(index < 0 for index in value):
            raise ValueError("Substructure bond indices cannot be negative.")
        if len(value) != len(set(value)):
            raise ValueError("Substructure bond indices must be unique.")
        return tuple(sorted(value))


class MolecularSubstructureResolution(CanonicalModel):
    """Immutable evidence for one resolved molecular substructure pattern."""

    schema_version: CapabilityVersion = _SCHEMA_VERSION
    resolution_identifier: str
    graph_identifier: str
    query_smarts: str = Field(min_length=1, max_length=16_384)
    matches: tuple[MolecularSubstructureMatch, ...] = Field(min_length=1)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(
        cls,
        value: CapabilityVersion,
    ) -> CapabilityVersion:
        if value != _SCHEMA_VERSION:
            raise ValueError(
                "Advanced molecular geometry schema version must be exactly 1.0.0."
            )
        return value

    @field_validator("resolution_identifier", "graph_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(
            value,
            label="molecular substructure identifier",
        )

    @field_validator("query_smarts")
    @classmethod
    def normalize_smarts(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Substructure SMARTS cannot be blank.")
        return normalized

    @field_validator("matches")
    @classmethod
    def validate_matches(
        cls,
        value: tuple[MolecularSubstructureMatch, ...],
    ) -> tuple[MolecularSubstructureMatch, ...]:
        indices = [item.match_index for item in value]
        if sorted(indices) != list(range(len(value))):
            raise ValueError(
                "Substructure match indices must be contiguous from zero."
            )
        return tuple(sorted(value, key=lambda item: item.match_index))


class MolecularDistanceScanPoint(CanonicalModel):
    """One validated constrained-optimization point in a distance scan."""

    point_index: int = Field(ge=0)
    requested_distance_angstrom: float = Field(gt=0)
    observed_distance_angstrom: float = Field(gt=0)
    distance_residual_angstrom: float = Field(ge=0)
    conformer_identifier: str
    applied_force_constant: float = Field(gt=0)
    restrained_force_field_energy_kcal_per_mole: float
    unrestrained_force_field_energy_kcal_per_mole: float
    optimization_status: Literal["converged", "not_converged"]

    @field_validator(
        "requested_distance_angstrom",
        "observed_distance_angstrom",
        "distance_residual_angstrom",
        "applied_force_constant",
        "restrained_force_field_energy_kcal_per_mole",
        "unrestrained_force_field_energy_kcal_per_mole",
    )
    @classmethod
    def validate_numbers(cls, value: float) -> float:
        return _finite(value, label="Distance-scan value")

    @field_validator("conformer_identifier")
    @classmethod
    def validate_conformer_identifier(cls, value: str) -> str:
        return validate_identifier(
            value,
            label="distance-scan conformer identifier",
        )

    @model_validator(mode="after")
    def validate_residual(self) -> Self:
        expected = abs(
            self.requested_distance_angstrom
            - self.observed_distance_angstrom
        )
        if not math.isclose(
            self.distance_residual_angstrom,
            expected,
            rel_tol=0,
            abs_tol=1e-8,
        ):
            raise ValueError(
                "Distance-scan residual does not match requested and "
                "observed coordinates."
            )
        return self


class MolecularDistanceScan(CanonicalModel):
    """Reaction-coordinate scan over one explicit molecular bond."""

    schema_version: CapabilityVersion = _SCHEMA_VERSION
    scan_identifier: str
    graph_identifier: str
    conformer_set_identifier: str
    atom_index_a: int = Field(ge=0)
    atom_index_b: int = Field(ge=0)
    bond_index: int = Field(ge=0)
    start_distance_angstrom: float = Field(gt=0)
    stop_distance_angstrom: float = Field(gt=0)
    point_count: int = Field(ge=2, le=100)
    force_field: Literal["mmff94s", "uff"]
    force_constant: float = Field(gt=0)
    maximum_force_constant: float = Field(gt=0)
    coordinate_tolerance_angstrom: float = Field(gt=0, le=0.1)
    points: tuple[MolecularDistanceScanPoint, ...] = Field(min_length=2)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(
        cls,
        value: CapabilityVersion,
    ) -> CapabilityVersion:
        if value != _SCHEMA_VERSION:
            raise ValueError(
                "Advanced molecular geometry schema version must be exactly 1.0.0."
            )
        return value

    @field_validator(
        "scan_identifier",
        "graph_identifier",
        "conformer_set_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(
            value,
            label="molecular distance-scan identifier",
        )

    @field_validator(
        "start_distance_angstrom",
        "stop_distance_angstrom",
        "force_constant",
        "maximum_force_constant",
        "coordinate_tolerance_angstrom",
    )
    @classmethod
    def validate_numbers(cls, value: float) -> float:
        return _finite(value, label="Distance-scan parameter")

    @field_validator("points")
    @classmethod
    def validate_points(
        cls,
        value: tuple[MolecularDistanceScanPoint, ...],
    ) -> tuple[MolecularDistanceScanPoint, ...]:
        indices = [item.point_index for item in value]
        if sorted(indices) != list(range(len(value))):
            raise ValueError(
                "Distance-scan point indices must be contiguous from zero."
            )
        return tuple(sorted(value, key=lambda item: item.point_index))

    @model_validator(mode="after")
    def validate_scan(self) -> Self:
        if self.atom_index_a == self.atom_index_b:
            raise ValueError(
                "A distance scan requires two distinct atoms."
            )
        if self.start_distance_angstrom == self.stop_distance_angstrom:
            raise ValueError(
                "A distance scan requires distinct start and stop distances."
            )
        if len(self.points) != self.point_count:
            raise ValueError(
                "Distance-scan point count does not match its declared count."
            )
        if self.maximum_force_constant < self.force_constant:
            raise ValueError(
                "Maximum distance-restraint force constant cannot be "
                "smaller than the initial force constant."
            )
        if any(
            point.optimization_status != "converged"
            for point in self.points
        ):
            raise ValueError(
                "An accepted distance scan cannot contain a "
                "nonconverged point."
            )
        if any(
            point.distance_residual_angstrom
            > self.coordinate_tolerance_angstrom + 1e-10
            for point in self.points
        ):
            raise ValueError(
                "A distance-scan point exceeds the declared "
                "coordinate tolerance."
            )
        if any(
            point.applied_force_constant < self.force_constant
            or point.applied_force_constant > self.maximum_force_constant
            for point in self.points
        ):
            raise ValueError(
                "A scan point used a force constant outside the "
                "declared adaptive range."
            )
        first = self.points[0].requested_distance_angstrom
        last = self.points[-1].requested_distance_angstrom
        if not math.isclose(
            first,
            self.start_distance_angstrom,
            rel_tol=0,
            abs_tol=1e-10,
        ):
            raise ValueError(
                "The first scan point does not match the declared start distance."
            )
        if not math.isclose(
            last,
            self.stop_distance_angstrom,
            rel_tol=0,
            abs_tol=1e-10,
        ):
            raise ValueError(
                "The final scan point does not match the declared stop distance."
            )
        return self
