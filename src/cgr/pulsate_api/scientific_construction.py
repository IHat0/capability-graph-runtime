"""Identity-only molecular construction composed with existing numerical engines.

No downloaded conformer enters construction. Electronic defaults are disclosed
workflow policy, not values attributed to the scientist or chemical identity proof.
"""
from __future__ import annotations

import json
import math
import re
from urllib.parse import quote

from .natural_language import OpenAICompatibleModelProvider
from .scientific_acquisition import _fetch_bytes
from .scientific_runtime import ScientificCapabilityFailure, ScientificCapabilityOutcome, ScientistCapabilityRegistry
from cgr.electronic_structure.contracts import ElectronicAtom, ElectronicMolecule, ElectronicHartreeFockResult, ElectronicStructureConfiguration
from .phase8_scientific_handlers import (_NativeRunner, _VERSION, _stable_identifier,
    ScientificEngineHandler, MinimalSceneHandler, CONFIGURATION_DEFINE, HARTREE_FOCK,
    SMILES_PARSE, PREPARE, CONFORMER_GENERATION)


def construction_computation_decision(reference):
    """Choose methods for ordinary structural construction, not by model availability.

    This is not a claim that RHF proves weak correlation. Explicit correlated-energy
    requests continue through the existing ground-state/quantum capability profiles.
    """
    if (not reference['converged'] or reference['reference_method'] != 'rhf'
        or reference['alpha_electron_count'] != reference['beta_electron_count']
        or any(abs(value - round(value)) > 1e-8 for value in reference['orbital_occupations_alpha'])):
        raise ScientificCapabilityFailure('construction_reference_insufficient',
            'The closed-shell classical reference is not valid. Resolve the electronic state or request a suitable higher-level analysis; no quantum job is selected automatically.')
    energies, occupations = reference['orbital_energies_alpha_hartree'], reference['orbital_occupations_alpha']
    occupied = [energy for energy, occupation in zip(energies, occupations, strict=True) if occupation > 0]
    virtual = [energy for energy, occupation in zip(energies, occupations, strict=True) if occupation == 0]
    gap = min(virtual) - max(occupied) if occupied and virtual else None
    return {
        'policy': 'structural_construction_classical_sufficiency_v1',
        'task': 'molecular_construction', 'selected_compute': 'classical',
        'quantum_execution_target': 'none', 'quantum_selected': False,
        'reason': 'Classical computation is sufficient for molecular graph construction, conformer generation, force-field geometry optimization and the requested ordinary electronic reference analysis. No correlated-energy, electronic degeneracy or quantum-algorithm problem was requested; quantum availability alone is not a scientific reason to invoke it.',
        'reference_converged': reference['converged'], 'reference_method': reference['reference_method'],
        'homo_lumo_gap_hartree': gap,
        'limitations': ['RHF convergence, integer occupations and the orbital gap do not establish absence of strong correlation. This minimal-basis analysis does not claim accurate correlated energetics or quantum advantage. An explicitly requested correlated subproblem requires its own validated scientific controls.'],
    }


def literal_entity(question, provider):
    if provider is None:
        raise ValueError('A configured language model is required to interpret the construction request.')
    messages = [
        {'role': 'system', 'content': 'Extract the one existing named chemical entity whose structure is requested. Return JSON {"name": "exact substring"}. Return {} if ambiguous. Never invent a name, SMILES, formula, coordinates, or chemical result.'},
        {'role': 'user', 'content': question},
    ]
    structured = getattr(provider, 'complete_structured', None)
    schema = {'type': 'object', 'properties': {'name': {'type': 'string'}},
              'required': ['name'], 'additionalProperties': False}
    response = json.loads(structured(messages, schema) if structured else provider.complete(messages))
    name = response.get('name')
    if isinstance(name, str) and name not in question:
        matches = list(re.finditer(re.escape(name), question, re.IGNORECASE))
        if len(matches) == 1:
            name = matches[0].group()
    if not isinstance(name, str) or not 0 < len(name) <= 128 or name not in question:
        raise ValueError('The requested chemical identity is ambiguous; provide one exact chemical name.')
    return name


class MolecularConstructionHandler:
    def __init__(self, store, rdkit_adapter, provider=None, fetch=_fetch_bytes):
        self.runner, self.adapter = _NativeRunner(store), rdkit_adapter
        self.provider, self.fetch = provider, fetch

    def execute(self, *, invocation, objective, record):
        from rdkit import Chem
        from rdkit.Chem import rdMolDescriptors
        from cgr.molecular.cheminformatics import MolecularConformerSet
        from cgr.molecular.rdkit_adapter import _molecule_from_graph
        name = literal_entity(objective.original_request, self.provider or OpenAICompatibleModelProvider.from_environment())
        url = 'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/' + quote(name, safe='') + '/property/IsomericSMILES,Charge/JSON'
        payload = self.fetch(url, 'application/json', 128 * 1024)
        rows = json.loads(payload).get('PropertyTable', {}).get('Properties', [])
        if len(rows) != 1:
            raise ValueError('The chemical name does not resolve uniquely; an exact identity is required.')
        identity = rows[0]
        smiles = identity.get('SMILES') or identity.get('IsomericSMILES')
        source = self.runner.write_json(artifact_type='molecular_identity_specification', payload=payload,
            producer='molecular.identity_construct', execution_identifier=invocation.invocation_identifier,
            metadata={'source_url': url, 'coordinate_source': 'none', 'requested_name': name})
        parsed = self.runner.invoke(self.adapter, SMILES_PARSE, parameters={'smiles': smiles},
            execution_identifier=invocation.invocation_identifier+'-parse', objective=objective)[0]
        prepared = self.runner.invoke(self.adapter, PREPARE, inputs=(parsed,), parameters={'add_hydrogens': True},
            execution_identifier=invocation.invocation_identifier+'-prepare', objective=objective)[0]
        generated = self.runner.invoke(self.adapter, CONFORMER_GENERATION, inputs=(prepared,), parameters={
            'conformer_count': 8, 'random_seed': 29, 'maximum_iterations': 2000,
            'prune_rms_threshold': -1.0, 'optimize': True},
            execution_identifier=invocation.invocation_identifier+'-conformers', objective=objective)[0]
        conformers = MolecularConformerSet.model_validate_json(self.runner.store.read(generated))
        converged = [c for c in conformers.conformers if c.optimization_status == 'converged' and c.energy is not None and math.isfinite(c.energy)]
        if not converged:
            raise ValueError('No force-field conformer converged; no geometry is accepted.')
        chosen = min(converged, key=lambda c: (c.energy, c.conformer_index))
        graph = conformers.source_graph
        molecule = _molecule_from_graph(graph)
        for atom in molecule.GetAtoms():
            atom.SetAtomMapNum(0)
        if graph.component_count != 1 or any(a.GetNumRadicalElectrons() for a in molecule.GetAtoms()):
            raise ValueError('Automatic closed-shell construction does not support disconnected or radical species.')
        if graph.formal_charge != identity['Charge']:
            raise ValueError('Constructed formal charge conflicts with the identity specification.')
        electrons = sum(a.atomic_number for a in graph.atoms) - graph.formal_charge
        if electrons < 2 or electrons % 2 or len(graph.atoms) > 64:
            raise ValueError('This molecule is outside the bounded closed-shell electronic-construction policy.')
        formula = rdMolDescriptors.CalcMolFormula(molecule)
        assumptions = (
            'Known identity reconstructed computationally; not a new molecular identity or physical synthesis.',
            'One source-declared chemical state is retained; no pH-dependent state population was computed.',
            'Lowest converged force-field energy among the sampled geometries is selected, not a proof of the global minimum.',
            'Gas-phase STO-3G RHF is an inexpensive full-molecule reference, not a quantitatively converged chemical prediction.',
            'Classical construction and ordinary RHF reference analysis do not require a quantum subproblem. No active space, VQE or hardware execution is selected.',
        )
        geometry_id = _stable_identifier('constructed-geometry', generated.content_sha256, chosen.conformer_index)
        atoms = tuple(ElectronicAtom(atom_index=a.atom_index, atomic_number=a.atomic_number,
            element_symbol=a.element_symbol, x_angstrom=c.x, y_angstrom=c.y, z_angstrom=c.z,
            source_atom_index=a.atom_index) for a, c in zip(graph.atoms, chosen.coordinates, strict=True))
        structure = self.runner.write_json(artifact_type='molecular_structure', payload=json.dumps({
            'schema_version': '2.0.0', 'geometry_identifier': geometry_id, 'system_label': name,
            'molecular_formula': formula, 'coordinate_unit': 'angstrom',
            'atoms': [dict(atom_index=a.atom_index, atomic_number=a.atomic_number, element_symbol=a.element_symbol,
                x=a.x_angstrom, y=a.y_angstrom, z=a.z_angstrom) for a in atoms],
            'control_provenance': {'geometry': 'derived', 'charge': 'source_identity', 'spin': 'closed_shell_policy',
                'basis_set': 'audited_default', 'reference_method': 'audited_default'},
            'assumptions': assumptions,
        }).encode(), producer='molecular.identity_construct', execution_identifier=invocation.invocation_identifier,
            parents=(source, generated), metadata={'evidence_kind': 'generated_by_rdkit'})
        electronic_molecule = ElectronicMolecule(schema_version=_VERSION,
            molecule_identifier=_stable_identifier('electronic-molecule', structure.content_sha256),
            source_artifact_identifier=structure.artifact_identifier, source_geometry_identifier=geometry_id,
            atoms=atoms, molecular_charge=graph.formal_charge, source_formal_charge=graph.formal_charge,
            spin=0, electron_count=electrons, alpha_electron_count=electrons // 2, beta_electron_count=electrons // 2)
        electronic = self.runner.write_json(artifact_type='electronic_molecule',
            payload=electronic_molecule.to_canonical_json().encode(), producer='molecular.identity_construct',
            execution_identifier=invocation.invocation_identifier, parents=(structure,))
        conf = Chem.Conformer(len(graph.atoms))
        for c in chosen.coordinates:
            conf.SetAtomPosition(c.atom_index, (c.x, c.y, c.z))
        molecule.RemoveAllConformers()
        molecule.AddConformer(conf)
        sdf = self.runner.write_bytes(artifact_type='constructed_molecule_sdf', media_type='chemical/x-mdl-sdfile',
            payload=(Chem.MolToMolBlock(molecule)+'\n$$$$\n').encode(), producer='molecular.identity_construct',
            execution_identifier=invocation.invocation_identifier, parents=(source, generated),
            metadata={'display_label': name + ' · generated geometry', 'coordinate_source': 'generated_by_rdkit'})
        xyz = self.runner.write_bytes(artifact_type='constructed_molecule_xyz', media_type='chemical/x-xyz',
            payload=(str(len(atoms)) + '\nGenerated geometry in angstrom; ' + name + '\n' + '\n'.join(
                f'{a.element_symbol} {a.x_angstrom:.15g} {a.y_angstrom:.15g} {a.z_angstrom:.15g}' for a in atoms) + '\n').encode(),
            producer='molecular.identity_construct', execution_identifier=invocation.invocation_identifier,
            parents=(source, generated), metadata={'coordinate_source': 'generated_by_rdkit'})
        evidence = self.runner.write_json(artifact_type='molecular_construction_evidence', payload=json.dumps({
            'name': name, 'pubchem_cid': identity['CID'], 'formula': formula, 'formal_charge': graph.formal_charge,
            'atom_count_including_hydrogens': len(graph.atoms), 'canonical_smiles': Chem.MolToSmiles(Chem.RemoveHs(molecule), isomericSmiles=True),
            'selected_conformer': chosen.model_dump(mode='json'), 'force_field': conformers.force_field,
            'attempted_conformers': conformers.requested_conformer_count, 'generated_conformers': len(conformers.conformers),
            'converged_conformers': len(converged), 'coordinate_source': 'generated_by_rdkit',
            'sdf_artifact_identifier': sdf.artifact_identifier, 'xyz_artifact_identifier': xyz.artifact_identifier,
            'selection_rationale': 'Lowest finite force-field energy among all converged generated conformers, with conformer index as a deterministic tie-break; no global minimum claim.',
            'limitations': assumptions,
        }).encode(), producer='molecular.identity_construct', execution_identifier=invocation.invocation_identifier,
            parents=(source, parsed, prepared, generated, sdf, xyz, structure))
        outputs = (source, parsed, prepared, generated, structure, electronic, sdf, xyz, evidence)
        return ScientificCapabilityOutcome(output_artifacts=outputs, evidence_artifacts=outputs, limitations=assumptions,
            scientific_summary=f'Constructed {formula}: optimized {len(converged)} generated geometries; selected the lowest sampled force-field energy.')


class MolecularIdentityVerificationHandler:
    def __init__(self, store, fetch=_fetch_bytes):
        self.runner, self.fetch = _NativeRunner(store), fetch

    def execute(self, *, invocation, objective, record):
        from rdkit import Chem
        from rdkit.Chem import rdMolDescriptors
        import io
        evidence = next(r for r in record.artifact_references if r.artifact_type == 'molecular_construction_evidence')
        constructed = json.loads(self.runner.store.read(evidence))
        sdf = next(r for r in record.artifact_references if r.artifact_identifier == constructed['sdf_artifact_identifier'])
        molecule = next(Chem.ForwardSDMolSupplier(io.BytesIO(self.runner.store.read(sdf)), removeHs=False))
        url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{constructed['pubchem_cid']}/property/InChI,InChIKey,MolecularFormula,Charge/JSON"
        raw = self.fetch(url, 'application/json', 128 * 1024)
        reference = json.loads(raw)['PropertyTable']['Properties'][0]
        independent = Chem.AddHs(Chem.MolFromInchi(reference['InChI']))
        heavy = Chem.RemoveHs(molecule)
        reference_heavy = Chem.RemoveHs(independent)
        checks = {
            'same_record': reference['CID'] == constructed['pubchem_cid'],
            'formula': rdMolDescriptors.CalcMolFormula(molecule) == reference['MolecularFormula'],
            'charge': Chem.GetFormalCharge(molecule) == reference['Charge'],
            'atom_count': molecule.GetNumAtoms() == independent.GetNumAtoms(),
            'connectivity_bond_orders_stereochemistry': Chem.MolToSmiles(heavy, isomericSmiles=True) == Chem.MolToSmiles(reference_heavy, isomericSmiles=True),
            'inchikey': Chem.MolToInchiKey(heavy) == reference['InChIKey'],
            'coordinates_finite': all(math.isfinite(v) for p in molecule.GetConformer().GetPositions() for v in p),
            'coordinates_noncollapsed': all((molecule.GetConformer().GetAtomPosition(b.GetBeginAtomIdx()) - molecule.GetConformer().GetAtomPosition(b.GetEndAtomIdx())).Length() > 0.5 for b in molecule.GetBonds()),
        }
        ref = self.runner.write_json(artifact_type='molecular_identity_reference', payload=raw,
            producer='molecular.identity_verify', execution_identifier=invocation.invocation_identifier,
            parents=(sdf,), metadata={'source_url': url, 'retrieval_phase': 'after_geometry_construction'})
        report = self.runner.write_json(artifact_type='molecular_identity_verification', payload=json.dumps({
            'passed': all(checks.values()), 'checks': checks, 'reference_inchikey': reference['InChIKey'],
            'independence': 'Fresh post-construction retrieval and InChI graph reconstruction; same public database, not independent curation; no coordinates retrieved.',
        }).encode(), producer='molecular.identity_verify', execution_identifier=invocation.invocation_identifier, parents=(evidence,sdf,ref))
        if not all(checks.values()):
            raise ScientificCapabilityFailure('constructed_identity_mismatch', 'Generated graph or geometry failed independent post-construction verification.')
        return ScientificCapabilityOutcome(output_artifacts=(ref,report), evidence_artifacts=(ref,report))


class ConstructionComputationSelectionHandler:
    def __init__(self, store):
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record):
        hf = next(r for r in record.artifact_references if r.artifact_type == 'electronic_hartree_fock_result')
        decision = construction_computation_decision(json.loads(self.runner.store.read(hf)))
        evidence = self.runner.write_json(artifact_type='computation_selection_decision',
            payload=json.dumps(decision).encode(), producer='electronic.computation_select',
            execution_identifier=invocation.invocation_identifier, parents=(hf,))
        return ScientificCapabilityOutcome(output_artifacts=(evidence,), evidence_artifacts=(evidence,),
            scientific_summary=decision['reason'], limitations=tuple(decision['limitations']))


class ConstructionVerificationHandler:
    def __init__(self, store):
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record):
        refs = {r.artifact_type: r for r in record.artifact_references}
        def read(kind):
            return json.loads(self.runner.store.read(refs[kind]))
        molecule = ElectronicMolecule.model_validate(read('electronic_molecule'))
        configuration = ElectronicStructureConfiguration.model_validate(read('electronic_structure_configuration'))
        hf = ElectronicHartreeFockResult.model_validate(read('electronic_hartree_fock_result'))
        built, identity, selection = (read(kind) for kind in (
            'molecular_construction_evidence', 'molecular_identity_verification', 'computation_selection_decision'))
        checks = {
            'identity_verified_after_construction': identity['passed'] and all(identity['checks'].values()),
            'reference_converged': hf.converged,
            'same_molecule': hf.molecule_identifier == molecule.molecule_identifier,
            'same_configuration': hf.configuration_identifier == configuration.configuration_identifier,
            'electron_count': hf.electron_count == molecule.electron_count,
            'full_molecule_reference': hf.reference_method == configuration.reference_method == 'rhf',
            'energy_components': math.isclose(hf.total_energy_hartree,
                hf.electronic_energy_hartree + hf.nuclear_repulsion_energy_hartree, abs_tol=1e-8),
            'generated_geometry': built['coordinate_source'] == 'generated_by_rdkit',
            'optimized_geometry': built['selected_conformer']['optimization_status'] == 'converged',
            'classical_selection': selection['selected_compute'] == 'classical' and not selection['quantum_selected']
                and objective.quantum_execution_target == 'none',
        }
        if not all(checks.values()):
            raise ScientificCapabilityFailure('construction_verification_failed', 'Generated molecular evidence failed blocking verification: ' + ', '.join(k for k,v in checks.items() if not v))
        parents = tuple(refs[k] for k in ('molecular_construction_evidence', 'molecular_identity_verification',
            'electronic_molecule', 'electronic_structure_configuration', 'electronic_hartree_fock_result', 'computation_selection_decision'))
        receipt = self.runner.write_json(artifact_type='molecular_construction_execution_receipt',
            payload=json.dumps({'verified': True, 'checks': checks, 'hartree_fock': hf.model_dump(mode='json'),
                'scientific_controls': configuration.model_dump(mode='json'), 'computation_selection': selection,
                'geometry_artifact_identifier': built['sdf_artifact_identifier'],
                'workflow': ['identity resolution', 'molecular construction', '3D conformer generation',
                    'geometry optimization', 'classical electronic analysis', 'independent identity verification'],
            }).encode(), producer='scientific_verification.molecular_construction',
            execution_identifier=invocation.invocation_identifier, parents=parents)
        report = self.runner.write_json(artifact_type='scientific_verification_report',
            payload=json.dumps({'passed': True, 'checks': checks, 'receipt_artifact_identifier': receipt.artifact_identifier}).encode(),
            producer='scientific_verification.molecular_construction', execution_identifier=invocation.invocation_identifier,
            parents=(*parents, receipt))
        return ScientificCapabilityOutcome(output_artifacts=(receipt,report), evidence_artifacts=(receipt,report),
            verified=True, scientific_summary='Generated structure, converged classical electronic analysis and post-construction identity all passed blocking verification.')


def molecular_construction_registry(*, store, rdkit_adapter, pyscf_adapter, qiskit_adapter):
    del qiskit_adapter  # Kept in the production composition; not needed for ordinary construction.
    handlers = {
        'electronic.configuration_define': ScientificEngineHandler(pyscf_adapter, CONFIGURATION_DEFINE,
            parameters={'basis_set': 'sto-3g', 'reference_method': 'rhf', 'convergence_tolerance': 1e-10,
                'maximum_iterations': 200, 'direct_scf': True, 'density_fitting': False,
                'symmetry': False, 'initial_guess': 'minao'}),
        'electronic.hartree_fock': ScientificEngineHandler(pyscf_adapter, HARTREE_FOCK),
        'electronic.computation_select': ConstructionComputationSelectionHandler(store),
        'scientific_verification.molecular_construction': ConstructionVerificationHandler(store),
        'molecular.scene_project': MinimalSceneHandler(store),
        'molecular.identity_construct': MolecularConstructionHandler(store, rdkit_adapter),
        'molecular.identity_verify': MolecularIdentityVerificationHandler(store),
    }
    return ScientistCapabilityRegistry(handlers)
