"""Offline independent checks/rendering of exported construction evidence.

Usage: python tests/validate_constructed_structure.py PATH_TO_BROWSER_EVIDENCE
No identity/coordinates are downloaded or supplied to construction by this check.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from rdkit import Chem
from rdkit.Chem import rdMolDescriptors


def validate(root: Path):
    workspace = json.loads((root / 'visualization.json').read_text())
    manifest = json.loads((root / 'artifact-manifest.json').read_text())
    references = {item['artifact_type']: item for item in workspace['export_items']}
    for item in manifest:
        payload = (root / 'artifacts' / item['identifier']).read_bytes()
        expected = next(ref['content_sha256'] for ref in workspace['export_items'] if ref['artifact_identifier'] == item['identifier'])
        assert hashlib.sha256(payload).hexdigest() == item['sha256'] == expected

    def path(kind):
        return root / 'artifacts' / references[kind]['artifact_identifier']

    def document(kind):
        return json.loads(path(kind).read_bytes())

    source, reference = document('molecular_identity_specification'), document('molecular_identity_reference')
    # The two public payloads contain properties only, not conformer coordinates.
    assert set(source) == set(reference) == {'PropertyTable'}
    ref = reference['PropertyTable']['Properties'][0]
    built = document('molecular_construction_evidence')
    molecule = next(Chem.SDMolSupplier(str(path('constructed_molecule_sdf')), removeHs=False))
    assert molecule is not None
    independently_rebuilt = Chem.AddHs(Chem.MolFromInchi(ref['InChI']))
    assert rdMolDescriptors.CalcMolFormula(molecule) == ref['MolecularFormula']
    assert Chem.GetFormalCharge(molecule) == ref['Charge']
    assert molecule.GetNumAtoms() == independently_rebuilt.GetNumAtoms()
    assert Chem.MolToSmiles(Chem.RemoveHs(molecule)) == Chem.MolToSmiles(Chem.RemoveHs(independently_rebuilt))
    assert Chem.MolToInchiKey(Chem.RemoveHs(molecule)) == ref['InChIKey']
    coordinates = molecule.GetConformer().GetPositions()
    original = np.array([[c['x'], c['y'], c['z']] for c in built['selected_conformer']['coordinates']])
    # V2000 serializes four decimal places; compare to the retained full precision.
    assert np.max(np.abs(coordinates - original)) <= 5.01e-5
    assert np.isfinite(coordinates).all()
    assert all(np.linalg.norm(coordinates[b.GetBeginAtomIdx()] - coordinates[b.GetEndAtomIdx()]) > .5 for b in molecule.GetBonds())
    if 'constructed_molecule_xyz' in references:
        lines = path('constructed_molecule_xyz').read_text().splitlines()
        assert int(lines[0]) == molecule.GetNumAtoms()
        rows = [line.split() for line in lines[2:]]
        assert [row[0] for row in rows] == [atom.GetSymbol() for atom in molecule.GetAtoms()]
        assert np.allclose(np.array([[float(value) for value in row[1:]] for row in rows]), original, rtol=0, atol=1e-12)
    if 'molecular_construction_execution_receipt' in references:
        decision = document('computation_selection_decision')
        assert decision['selected_compute'] == 'classical' and decision['quantum_execution_target'] == 'none'
        assert decision['quantum_selected'] is False
        receipt = document('molecular_construction_execution_receipt')
        assert receipt['verified'] and all(receipt['checks'].values())
        assert receipt['hartree_fock']['converged']
        assert not any(kind.startswith('quantum_') or kind == 'variational_ground_state_result' for kind in references)
    shutil.copyfile(path('constructed_molecule_sdf'), root / 'generated-molecule.sdf')
    xyz = '\n'.join([str(molecule.GetNumAtoms()), 'Pulsate-generated geometry; Cartesian coordinates in angstrom'] +
        [f'{atom.GetSymbol()} {point[0]:.9f} {point[1]:.9f} {point[2]:.9f}' for atom, point in zip(molecule.GetAtoms(), original, strict=True)])
    (root / 'generated-coordinates.xyz').write_text(xyz + '\n')
    colors = {'H': '#b8bec7', 'C': '#334155', 'N': '#2563eb', 'O': '#ef4444'}
    figure = plt.figure(figsize=(10, 8))
    axis = figure.add_subplot(111, projection='3d')
    for bond in molecule.GetBonds():
        points = coordinates[[bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()]]
        axis.plot(*points.T, color='#94a3b8', linewidth=2)
    for atom, point in zip(molecule.GetAtoms(), coordinates, strict=True):
        axis.scatter(*point, color=colors.get(atom.GetSymbol(), '#a855f7'), s=70 if atom.GetSymbol() == 'H' else 230)
        if atom.GetSymbol() != 'H':
            axis.text(*point, atom.GetSymbol(), fontsize=9)
    axis.set_box_aspect(np.maximum(np.ptp(coordinates, axis=0), 1))
    axis.set_xlabel('x (angstrom)'); axis.set_ylabel('y (angstrom)'); axis.set_zlabel('z (angstrom)')
    axis.set_title(f"{built['name']} · exported generated geometry\nIndependent RDKit SDF read + Matplotlib 3D rendering")
    figure.savefig(root / 'independent-render.png', dpi=180, bbox_inches='tight')
    plt.close(figure)
    result = {'passed': True, 'formula': ref['MolecularFormula'], 'charge': ref['Charge'],
        'atom_count': molecule.GetNumAtoms(), 'bond_count': molecule.GetNumBonds(),
        'inchikey': ref['InChIKey'], 'all_exported_hashes_verified': True,
        'maximum_serialization_coordinate_error_angstrom': float(np.max(np.abs(coordinates-original))),
        'reference_contains_no_3d_coordinates': True, 'independent_render': 'independent-render.png'}
    (root / 'independent-validation.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == '__main__':
    validate(Path(sys.argv[1]))
