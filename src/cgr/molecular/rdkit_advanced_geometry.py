"""RDKit-backed advanced geometry operations for Phase 8 scientific autonomy."""

from __future__ import annotations

import hashlib
import math

from cgr.kernel.contracts import CapabilityVersion

from .advanced_geometry import (
    MolecularDistanceScan,
    MolecularDistanceScanPoint,
    MolecularSubstructureMatch,
    MolecularSubstructureResolution,
)
from .cheminformatics import (
    MolecularConformer,
    MolecularConformerSet,
    MolecularCoordinate,
    MolecularGraph,
)


_SCHEMA_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
_MAXIMUM_SCAN_POINTS = 100
_MAXIMUM_SMARTS_LENGTH = 16_384


class RDKitAdvancedGeometryError(ValueError):
    """Controlled failure of an advanced RDKit molecular operation."""


def _stable_identifier(prefix: str, *values: object) -> str:
    encoded = "\x1f".join(str(value) for value in values).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:32]}"


def _rounded(value: float) -> float:
    return round(float(value), 10)


def _rdkit_modules() -> tuple[object, object, object]:
    from rdkit import Chem
    from rdkit.Chem import AllChem, rdMolTransforms

    return Chem, AllChem, rdMolTransforms


def _molecule_from_graph(graph: MolecularGraph) -> object:
    Chem, _, _ = _rdkit_modules()

    parameters = Chem.SmilesParserParams()
    parameters.sanitize = True
    parameters.removeHs = False

    molecule = Chem.MolFromSmiles(
        graph.mapped_smiles,
        parameters,
    )
    if molecule is None:
        raise RDKitAdvancedGeometryError(
            "The molecular graph exchange representation is invalid."
        )

    map_to_index: dict[int, int] = {}
    for atom in molecule.GetAtoms():
        atom_map = int(atom.GetAtomMapNum())
        if atom_map <= 0 or atom_map in map_to_index:
            raise RDKitAdvancedGeometryError(
                "The molecular graph atom mapping is invalid."
            )
        map_to_index[atom_map] = int(atom.GetIdx())

    expected_maps = tuple(
        atom.atom_map_number
        for atom in graph.atoms
    )
    if set(expected_maps) != set(map_to_index):
        raise RDKitAdvancedGeometryError(
            "The molecular graph atom mapping is incomplete."
        )

    order = [
        map_to_index[atom_map]
        for atom_map in expected_maps
    ]
    molecule = Chem.RenumberAtoms(molecule, order)

    if int(molecule.GetNumAtoms()) != len(graph.atoms):
        raise RDKitAdvancedGeometryError(
            "The molecular graph atom count changed during reconstruction."
        )

    return molecule


def resolve_smarts_substructure(
    graph: MolecularGraph,
    *,
    query_smarts: str,
    require_unique: bool = False,
) -> MolecularSubstructureResolution:
    """Resolve a chemically meaningful SMARTS pattern to stable graph indices."""

    if not isinstance(graph, MolecularGraph):
        raise TypeError(
            "Substructure resolution requires a MolecularGraph."
        )

    smarts = query_smarts.strip()
    if not smarts or len(smarts) > _MAXIMUM_SMARTS_LENGTH:
        raise RDKitAdvancedGeometryError(
            "The substructure SMARTS query is invalid."
        )

    Chem, _, _ = _rdkit_modules()
    molecule = _molecule_from_graph(graph)
    query = Chem.MolFromSmarts(smarts)

    if query is None or int(query.GetNumAtoms()) <= 0:
        raise RDKitAdvancedGeometryError(
            "The substructure SMARTS query cannot be parsed."
        )

    raw_matches = tuple(
        tuple(int(index) for index in match)
        for match in molecule.GetSubstructMatches(
            query,
            uniquify=True,
        )
    )

    if not raw_matches:
        raise RDKitAdvancedGeometryError(
            "The requested molecular substructure does not resolve."
        )

    if require_unique and len(raw_matches) != 1:
        raise RDKitAdvancedGeometryError(
            "The requested molecular substructure is ambiguous."
        )

    graph_bonds = {
        frozenset(
            (
                bond.atom_index_a,
                bond.atom_index_b,
            )
        ): bond.bond_index
        for bond in graph.bonds
    }

    matches: list[MolecularSubstructureMatch] = []
    for match_index, atom_indices in enumerate(raw_matches):
        atom_set = set(atom_indices)
        bond_indices = tuple(
            sorted(
                bond_index
                for endpoints, bond_index in graph_bonds.items()
                if endpoints.issubset(atom_set)
            )
        )
        matches.append(
            MolecularSubstructureMatch(
                match_index=match_index,
                atom_indices=atom_indices,
                bond_indices=bond_indices,
            )
        )

    return MolecularSubstructureResolution(
        resolution_identifier=_stable_identifier(
            "molecular-substructure",
            graph.fingerprint,
            smarts,
            raw_matches,
        ),
        graph_identifier=graph.graph_identifier,
        query_smarts=smarts,
        matches=tuple(matches),
    )


def _scan_distances(
    start: float,
    stop: float,
    point_count: int,
) -> tuple[float, ...]:
    if point_count < 2 or point_count > _MAXIMUM_SCAN_POINTS:
        raise RDKitAdvancedGeometryError(
            "Distance-scan point count is outside its allowed range."
        )

    if (
        not math.isfinite(start)
        or not math.isfinite(stop)
        or start <= 0
        or stop <= 0
        or math.isclose(start, stop, rel_tol=0, abs_tol=1e-12)
    ):
        raise RDKitAdvancedGeometryError(
            "Distance-scan endpoints are invalid."
        )

    step = (stop - start) / float(point_count - 1)
    return tuple(
        _rounded(
            stop
            if index == point_count - 1
            else start + step * index
        )
        for index in range(point_count)
    )


def constrained_bond_distance_scan(
    graph: MolecularGraph,
    *,
    atom_index_a: int,
    atom_index_b: int,
    start_distance_angstrom: float,
    stop_distance_angstrom: float,
    point_count: int,
    random_seed: int = 7,
    maximum_iterations: int = 500,
    force_constant: float = 1000.0,
    maximum_force_constant: float = 1_000_000.0,
    coordinate_tolerance_angstrom: float = 0.01,
) -> tuple[MolecularConformerSet, MolecularDistanceScan]:
    """Generate a deterministic, tolerance-enforced bond-distance scan."""

    if not isinstance(graph, MolecularGraph):
        raise TypeError(
            "A constrained distance scan requires a MolecularGraph."
        )

    atom_count = len(graph.atoms)
    if (
        isinstance(atom_index_a, bool)
        or isinstance(atom_index_b, bool)
        or not isinstance(atom_index_a, int)
        or not isinstance(atom_index_b, int)
        or atom_index_a < 0
        or atom_index_b < 0
        or atom_index_a >= atom_count
        or atom_index_b >= atom_count
        or atom_index_a == atom_index_b
    ):
        raise RDKitAdvancedGeometryError(
            "Distance-scan atom indices are invalid."
        )

    if (
        isinstance(random_seed, bool)
        or not isinstance(random_seed, int)
        or random_seed < 0
        or random_seed > 2_147_483_647
    ):
        raise RDKitAdvancedGeometryError(
            "Distance-scan random seed is invalid."
        )

    if (
        isinstance(maximum_iterations, bool)
        or not isinstance(maximum_iterations, int)
        or maximum_iterations < 1
        or maximum_iterations > 100_000
    ):
        raise RDKitAdvancedGeometryError(
            "Distance-scan iteration limit is invalid."
        )

    for value, label in (
        (force_constant, "initial force constant"),
        (maximum_force_constant, "maximum force constant"),
        (coordinate_tolerance_angstrom, "coordinate tolerance"),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) <= 0
        ):
            raise RDKitAdvancedGeometryError(
                f"Distance-scan {label} is invalid."
            )

    initial_force_constant = float(force_constant)
    maximum_force_constant = float(maximum_force_constant)
    coordinate_tolerance_angstrom = float(
        coordinate_tolerance_angstrom
    )

    if initial_force_constant > maximum_force_constant:
        raise RDKitAdvancedGeometryError(
            "Initial distance-restraint force constant exceeds its maximum."
        )
    if maximum_force_constant > 10_000_000.0:
        raise RDKitAdvancedGeometryError(
            "Distance-restraint maximum force constant is too large."
        )
    if coordinate_tolerance_angstrom > 0.1:
        raise RDKitAdvancedGeometryError(
            "Distance-scan coordinate tolerance is too loose."
        )

    left, right = sorted(
        (atom_index_a, atom_index_b)
    )
    matched_bonds = tuple(
        bond
        for bond in graph.bonds
        if bond.atom_index_a == left
        and bond.atom_index_b == right
    )

    if len(matched_bonds) != 1:
        raise RDKitAdvancedGeometryError(
            "A constrained bond scan requires one explicit graph bond."
        )

    distances = _scan_distances(
        float(start_distance_angstrom),
        float(stop_distance_angstrom),
        point_count,
    )

    _, AllChem, rdMolTransforms = _rdkit_modules()
    molecule = _molecule_from_graph(graph)

    embed = AllChem.ETKDGv3()
    embed.randomSeed = random_seed
    embed.numThreads = 1

    conformer_identifier = int(
        AllChem.EmbedMolecule(
            molecule,
            embed,
        )
    )
    if conformer_identifier < 0:
        raise RDKitAdvancedGeometryError(
            "RDKit could not generate the initial scan geometry."
        )

    if AllChem.MMFFHasAllMoleculeParams(molecule):
        force_field_name = "mmff94s"
        properties = AllChem.MMFFGetMoleculeProperties(
            molecule,
            mmffVariant="MMFF94s",
        )
        if properties is None:
            raise RDKitAdvancedGeometryError(
                "RDKit could not construct MMFF94s molecular properties."
            )
    elif AllChem.UFFHasAllMoleculeParams(molecule):
        force_field_name = "uff"
        properties = None
    else:
        raise RDKitAdvancedGeometryError(
            "The molecule is unsupported by both MMFF94s and UFF."
        )

    def build_force_field(
        *,
        restraint_force_constant: float | None,
        target_distance: float,
    ) -> object:
        if force_field_name == "mmff94s":
            field = AllChem.MMFFGetMoleculeForceField(
                molecule,
                properties,
                confId=conformer_identifier,
            )
            if field is None:
                raise RDKitAdvancedGeometryError(
                    "MMFF94s force-field construction failed."
                )
            if restraint_force_constant is not None:
                field.MMFFAddDistanceConstraint(
                    left,
                    right,
                    False,
                    target_distance,
                    target_distance,
                    restraint_force_constant,
                )
        else:
            field = AllChem.UFFGetMoleculeForceField(
                molecule,
                confId=conformer_identifier,
            )
            if field is None:
                raise RDKitAdvancedGeometryError(
                    "UFF force-field construction failed."
                )
            if restraint_force_constant is not None:
                field.UFFAddDistanceConstraint(
                    left,
                    right,
                    False,
                    target_distance,
                    target_distance,
                    restraint_force_constant,
                )

        field.Initialize()
        return field

    conformers: list[MolecularConformer] = []
    scan_points: list[MolecularDistanceScanPoint] = []

    for point_index, requested_distance in enumerate(distances):
        applied_force_constant = initial_force_constant
        accepted_field = None
        accepted_status = None
        observed_distance = None
        residual = None

        while True:
            conformer = molecule.GetConformer(conformer_identifier)

            # Re-anchor the reaction coordinate exactly before every attempt.
            rdMolTransforms.SetBondLength(
                conformer,
                left,
                right,
                requested_distance,
            )

            field = build_force_field(
                restraint_force_constant=applied_force_constant,
                target_distance=requested_distance,
            )

            status = int(
                field.Minimize(
                    maxIts=maximum_iterations,
                )
            )

            observed = _rounded(
                rdMolTransforms.GetBondLength(
                    conformer,
                    left,
                    right,
                )
            )
            current_residual = _rounded(
                abs(requested_distance - observed)
            )

            if (
                status == 0
                and current_residual
                <= coordinate_tolerance_angstrom
            ):
                accepted_field = field
                accepted_status = status
                observed_distance = observed
                residual = current_residual
                break

            if applied_force_constant >= maximum_force_constant:
                raise RDKitAdvancedGeometryError(
                    "A constrained scan point could not satisfy the "
                    "declared coordinate tolerance."
                )

            applied_force_constant = min(
                maximum_force_constant,
                applied_force_constant * 10.0,
            )

        if (
            accepted_field is None
            or accepted_status is None
            or observed_distance is None
            or residual is None
        ):
            raise RDKitAdvancedGeometryError(
                "Constrained scan acceptance state is incomplete."
            )

        restrained_energy = _rounded(
            accepted_field.CalcEnergy()
        )

        # Evaluate the underlying force field at the accepted constrained
        # geometry without the artificial restraint. Do not minimize here:
        # minimization would destroy the reaction-coordinate geometry.
        unrestrained_field = build_force_field(
            restraint_force_constant=None,
            target_distance=requested_distance,
        )
        unrestrained_energy = _rounded(
            unrestrained_field.CalcEnergy()
        )

        conformer = molecule.GetConformer(conformer_identifier)

        coordinates = tuple(
            MolecularCoordinate(
                atom_index=atom_index,
                x=_rounded(
                    conformer.GetAtomPosition(atom_index).x
                ),
                y=_rounded(
                    conformer.GetAtomPosition(atom_index).y
                ),
                z=_rounded(
                    conformer.GetAtomPosition(atom_index).z
                ),
            )
            for atom_index in range(atom_count)
        )

        resolved_conformer_identifier = _stable_identifier(
            "conformer-distance-scan",
            graph.fingerprint,
            left,
            right,
            point_index,
            requested_distance,
            coordinates,
        )

        conformers.append(
            MolecularConformer(
                conformer_identifier=resolved_conformer_identifier,
                conformer_index=point_index,
                coordinates=coordinates,
                energy=unrestrained_energy,
                energy_unit="kilocalorie_per_mole",
                optimization_status="converged",
            )
        )

        scan_points.append(
            MolecularDistanceScanPoint(
                point_index=point_index,
                requested_distance_angstrom=requested_distance,
                observed_distance_angstrom=observed_distance,
                distance_residual_angstrom=residual,
                conformer_identifier=resolved_conformer_identifier,
                applied_force_constant=applied_force_constant,
                restrained_force_field_energy_kcal_per_mole=(
                    restrained_energy
                ),
                unrestrained_force_field_energy_kcal_per_mole=(
                    unrestrained_energy
                ),
                optimization_status="converged",
            )
        )

    conformer_set = MolecularConformerSet(
        schema_version=_SCHEMA_VERSION,
        conformer_set_identifier=_stable_identifier(
            "molecular-conformer-set-distance-scan",
            graph.fingerprint,
            left,
            right,
            distances,
            random_seed,
            maximum_iterations,
            initial_force_constant,
            maximum_force_constant,
            coordinate_tolerance_angstrom,
        ),
        source_graph=graph,
        generation_method="rdkit_constrained_distance_scan",
        force_field=force_field_name,
        random_seed=random_seed,
        requested_conformer_count=point_count,
        conformers=tuple(conformers),
    )

    scan = MolecularDistanceScan(
        scan_identifier=_stable_identifier(
            "molecular-distance-scan",
            conformer_set.fingerprint,
            left,
            right,
            distances,
        ),
        graph_identifier=graph.graph_identifier,
        conformer_set_identifier=(
            conformer_set.conformer_set_identifier
        ),
        atom_index_a=left,
        atom_index_b=right,
        bond_index=matched_bonds[0].bond_index,
        start_distance_angstrom=distances[0],
        stop_distance_angstrom=distances[-1],
        point_count=point_count,
        force_field=force_field_name,
        force_constant=initial_force_constant,
        maximum_force_constant=maximum_force_constant,
        coordinate_tolerance_angstrom=(
            coordinate_tolerance_angstrom
        ),
        points=tuple(scan_points),
    )

    return conformer_set, scan

