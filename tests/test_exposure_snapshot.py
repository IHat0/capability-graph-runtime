"""Synthetic source/clock guards, never measured Human exposure evidence."""
import copy
import pytest
from test_functional_pharmacology import fixture
from cgr.pulsate_api.virtual_investigation import compatible_exposure_snapshots


def snapshot_fixture():
    assay, identity, sources, policy = fixture()
    datum = copy.deepcopy(assay['datum'])
    datum.update(category='early_pk', target_accession=None, evidence_type='sourced',
        context={'species': 'Human', 'endpoint': 'unbound_Cmax', 'concentration_basis': 'unbound',
            'organ': 'Peripheral Venous Blood', 'compartment': 'Plasma Unbound (Peripheral Venous Blood)',
            'regimen': 'Synthetic source contract, not a dosing recommendation'})
    item = {'inchikey': identity['inchikey'], 'species': 'Human', 'native_parameter': 'exposure_snapshot',
        'datum': datum, 'uncertainty': 'Synthetic estimate; not measured tissue',
        'applicability': 'Source and clock semantics only'}
    return identity, {'records': [item]}, sources, policy


def test_estimated_free_endpoint_is_not_a_measured_tissue_timecourse():
    identity, library, sources, policy = snapshot_fixture()
    result, excluded = compatible_exposure_snapshots(identity, library, sources, policy)
    assert not excluded and len(result) == 1
    point = result[0]
    assert point['classification'] == 'sourced'
    assert point['values_umol_l'] == [.12]
    assert point['times_h'] == [None]
    assert point['timecourse_available'] is False
    assert point['compartment_translation'] is None


@pytest.mark.parametrize('field,value', [('endpoint', 'total_Cmax'),
    ('concentration_basis', 'total'), ('species', 'Rat'), ('peak_time_h', -1.)])
def test_incompatible_exposure_or_fabricated_clock_refuses(field, value):
    identity, library, sources, policy = snapshot_fixture()
    library['records'][0]['datum']['context'][field] = value
    with pytest.raises(ValueError): compatible_exposure_snapshots(identity, library, sources, policy)


def test_tissue_translation_requires_actual_reviewed_source_anchors():
    identity, library, sources, policy = snapshot_fixture()
    library['records'][0]['compartment_translation'] = {
        'kind': 'passive_unbound_equivalence_assumption',
        'source_organ': 'Peripheral Venous Blood',
        'source_compartment': 'Plasma Unbound (Peripheral Venous Blood)',
        'organ': 'Heart', 'compartment': 'Interstitial Unbound',
        'rationale': 'Synthetic assumption only', 'limitations': 'Not measured tissue', 'source_anchors': []}
    with pytest.raises(ValueError, match='Unqualified'):
        compatible_exposure_snapshots(identity, library, sources, policy)


def test_changed_primary_bytes_are_refused():
    identity, library, sources, policy = snapshot_fixture()
    sources[next(iter(sources))] += b' '
    with pytest.raises(ValueError): compatible_exposure_snapshots(identity, library, sources, policy)
