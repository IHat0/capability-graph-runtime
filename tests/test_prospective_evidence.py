"""Synthetic eligibility contracts, not clinical/scientific accuracy evidence."""
from datetime import date
import json

import pytest

from cgr.pulsate_api.prospective_evidence import (
    EvidenceDatum, ProspectivePolicy, PROHIBITED, adjudicate, eligibility, verify_adjudication,
)
from cgr.pulsate_api.virtual_organism import canonical, digest


def datum(**updates):
    when = updates.get('publication_date', '1999-12-31')
    precision = updates.get('date_precision', 'year')
    publication = str(date.fromisoformat(str(when)).year) if precision == 'year' else when
    payload = canonical({'value': 1.2, 'unit': 'uM', 'published': publication,
        'released': updates.get('release_date')})
    values = dict(identifier='source-1', source_url='https://www.ebi.ac.uk/chembl/api/data/activity.json',
        source_record='synthetic-activity', source_sha256=digest(payload), value=1.2, unit='uM',
        value_pointer=('value',), category='quantitative_activity', evidence_type='measured',
        subject_inchikey='CANDIDATE', publication_date='1999-12-31', date_precision='year')
    if when is not None:
        values['original_source'] = dict(source_url=values['source_url'], source_record='synthetic-original',
            source_sha256=digest(payload), publication_date=when, date_precision=precision,
            source_kind='original_primary_record',reviewer='Synthetic fixture',review_basis='Synthetic primary-data contract only',
            publication_pointer=['published'], publication_value=publication,
            value_pointer=['value'], unit_pointer=['unit'])
        if updates.get('release_date'):
            values['original_source'].update(release_date=updates['release_date'],
                release_pointer=['released'], release_value=updates['release_date'])
    return EvidenceDatum(**dict(values, **updates)), payload


def policy(**updates):
    return ProspectivePolicy(**dict(cutoff=date(2000, 1, 1), cutoff_source='Synthetic reviewed protocol', **updates))


@pytest.mark.parametrize('category', sorted(PROHIBITED))
def test_outcome_classes_never_eligible_even_before_cutoff(category):
    d, _ = datum(category=category)
    assert not eligibility(d, policy(), 'CANDIDATE')['eligible']


def test_direct_activity_post_cutoff_or_unknown_is_quarantined():
    for updates in ({'publication_date': '2001-01-01', 'date_precision': 'day'},
                    {'publication_date': None, 'date_precision': 'unknown'},
                    {'release_date': '2001-01-01'}):
        d, _ = datum(**updates)
        assert not eligibility(d, policy(), 'CANDIDATE')['eligible']
    d, _ = datum()
    assert eligibility(d, policy(), 'CANDIDATE')['eligible']
    assert not eligibility(d, ProspectivePolicy(), 'CANDIDATE')['eligible']


def test_unrelated_ligand_is_hypothesis_evidence_not_historically_laundered():
    d, _ = datum(subject_inchikey='UNRELATED', publication_date=None, date_precision='unknown')
    decision = eligibility(d, policy(), 'CANDIDATE')
    assert decision['status'] == 'eligible_modern_general'
    assert not decision['historically_qualified']
    assert not eligibility(d, policy(allow_modern_general_knowledge=False), 'CANDIDATE')['eligible']


def test_general_annotation_is_explicitly_modern_and_foundational_identity_not_history():
    d, _ = datum(category='protein_annotation', subject_inchikey=None, publication_date=None, date_precision='unknown')
    assert eligibility(d, policy(), 'CANDIDATE')['status'] == 'eligible_modern_general'
    d, _ = datum(category='chemical_identity', publication_date=None, date_precision='unknown')
    assert eligibility(d, policy(), 'CANDIDATE')['status'] == 'eligible_foundational'


@pytest.mark.parametrize('kind', ['llm_estimate', 'narrative', 'docking_potency'])
def test_no_llm_measurements_or_docking_potency(kind):
    d, _ = datum(evidence_type=kind)
    assert not eligibility(d, policy(), 'CANDIDATE')['eligible']


@pytest.mark.parametrize('url', ['http://www.ebi.ac.uk/chembl/api/data/activity',
    'https://www.ebi.ac.uk.evil.invalid/chembl/api/data/activity',
    'https://www.ebi.ac.uk/retrospective-paper', 'https://user@www.ebi.ac.uk/chembl/api/data/activity'])
def test_source_allowlist_and_urls_fail_closed(url):
    d, _ = datum(source_url=url)
    assert not eligibility(d, policy(), 'CANDIDATE')['eligible']


def test_exact_source_and_independent_policy_replay_block_corruption():
    d, payload = datum()
    sources = {d.source_sha256: payload}
    report = adjudicate([d], sources, policy(), 'CANDIDATE')
    assert verify_adjudication(report, sources)['passed']
    report['decisions'][0]['status'] = 'excluded'
    with pytest.raises(ValueError, match='replay'): verify_adjudication(report, sources)
    with pytest.raises(ValueError, match='exact'): adjudicate([d.model_copy(update={'value': 99})], sources, policy(), 'CANDIDATE')
    with pytest.raises(ValueError, match='hash'): adjudicate([d], {d.source_sha256: b'{}'}, policy(), 'CANDIDATE')


def test_conflicting_same_identifier_and_unreviewed_cutoff_refused():
    d, payload = datum()
    with pytest.raises(ValueError, match='Duplicate'): adjudicate([d, d], {d.source_sha256: payload}, policy(), 'CANDIDATE')
    with pytest.raises(ValueError, match='provenance'): ProspectivePolicy(cutoff=date(2000, 1, 1))
    with pytest.raises(ValueError, match='year end'): datum(publication_date='1999-01-01')


def instant_policy():
    return ProspectivePolicy(cutoff=date(2000, 1, 1), cutoff_instant='2000-01-01T23:59:00Z',
        cutoff_source='Synthetic minute-precision challenge protocol')


def test_timezone_cutoff_and_conservative_day_year_precision():
    for kwargs in ({'cutoff_instant': '2000-01-01T23:59:00'},
                   {'cutoff': '2000-01-02', 'cutoff_instant': '2000-01-01T23:59:00Z'}):
        with pytest.raises(ValueError): ProspectivePolicy(cutoff_source='Synthetic review', **kwargs)
    for day, precision, permitted in [('1999-12-31', 'day', True), ('1999-12-31', 'year', True),
                                     ('2000-01-01', 'day', False), ('2000-12-31', 'year', False)]:
        d, _ = datum(publication_date=day, date_precision=precision)
        assert eligibility(d, instant_policy(), 'CANDIDATE')['eligible'] is permitted


def test_exact_cutoff_instant_inclusive_and_replayed_from_original_bytes():
    for timestamp, expected in [('2000-01-01T23:58:59Z', True), ('2000-01-01T23:59:00Z', True),
                                ('2000-01-01T23:59:01Z', False), ('2000-01-02T01:59:00+02:00', True)]:
        payload = canonical({'value': 1.2, 'unit': 'uM', 'published': timestamp})
        d, _ = datum(publication_date='2000-01-01', date_precision='day',
            source_sha256=digest(payload), original_source={
                'source_url':'https://www.ebi.ac.uk/chembl/api/data/activity.json',
                'source_record':'synthetic-original', 'source_sha256':digest(payload),
                'source_kind':'original_primary_record','reviewer':'Synthetic fixture','review_basis':'Synthetic primary-data contract only',
                'publication_date':'2000-01-01', 'date_precision':'instant', 'publication_instant':timestamp,
                'publication_pointer':['published'], 'publication_value':timestamp,
                'value_pointer':['value'], 'unit_pointer':['unit']})
        report = adjudicate([d], {digest(payload):payload}, instant_policy(), 'CANDIDATE')
        assert report['decisions'][0]['eligible'] is expected
        assert verify_adjudication(report, {digest(payload):payload})['passed']


def test_modern_wrapper_requires_exact_original_measurement_not_just_old_metadata():
    original, original_bytes = datum()
    wrapper_bytes = canonical({'value':1.2, 'unit':'uM', 'published':'2025-01-01'})
    wrapper = original.model_copy(update={'source_sha256':digest(wrapper_bytes),
        'publication_date':date(2025,1,1), 'date_precision':'day', 'release_date':date(2025,1,1),
        'wrapper_provenance':{'kind':'public_database_wrapper','not_primary':True,
            'source_url':original.source_url,'source_sha256':digest(wrapper_bytes),
            'original_source_sha256':digest(original_bytes),'record_identifier':'Synthetic wrapper',
            'reviewer':'Synthetic fixture','purpose':'Discovery only, not primary numerical evidence'}})
    sources = {digest(wrapper_bytes):wrapper_bytes, digest(original_bytes):original_bytes}
    report = adjudicate([wrapper], sources, instant_policy(), 'CANDIDATE')
    assert report['decisions'][0]['eligible']
    assert report['decisions'][0]['date_qualification_basis']=='original_measurement'
    assert verify_adjudication(report, sources)['passed']
    with pytest.raises(ValueError,match='non-primary wrapper'):
        adjudicate([wrapper.model_copy(update={'wrapper_provenance':None})],sources,instant_policy(),'CANDIDATE')
    assert not eligibility(wrapper.model_copy(update={'original_source':None}), instant_policy(), 'CANDIDATE')['eligible']
    with pytest.raises(ValueError,match='bytes are missing'):
        adjudicate([wrapper], {digest(wrapper_bytes):wrapper_bytes}, instant_policy(), 'CANDIDATE')
    for key, value in [('value',7), ('unit','nM'), ('published','1998')]:
        altered = json.loads(original_bytes)
        altered[key] = value
        changed = canonical(altered)
        trace = wrapper.original_source.model_copy(update={'source_sha256':digest(changed)})
        with pytest.raises(ValueError,match='original|Original'):
            adjudicate([wrapper.model_copy(update={'original_source':trace})],
                {digest(wrapper_bytes):wrapper_bytes,digest(changed):changed},instant_policy(),'CANDIDATE')


@pytest.mark.parametrize('category', ['physiology','protein_annotation','tissue_expression','pathway_model'])
def test_candidate_observation_cannot_be_laundered_as_general_knowledge(category):
    d, _ = datum(category=category)
    assert not eligibility(d, instant_policy(), 'CANDIDATE')['eligible']
    d, _ = datum(category=category, subject_inchikey=None, original_source=None,
        publication_date='2025-01-01',date_precision='day')
    assert eligibility(d, instant_policy(), 'CANDIDATE')['status']=='eligible_modern_general'


def test_converted_secondary_record_or_missing_original_review_is_not_primary():
    d, _ = datum()
    values = d.model_dump(mode='json')
    values['original_source']['source_kind'] = 'secondary_transcription'
    with pytest.raises(ValueError, match='secondary'): EvidenceDatum.model_validate(values)
    values['original_source']['source_kind'] = 'original_primary_record'
    values['original_source'].pop('reviewer')
    with pytest.raises(ValueError): EvidenceDatum.model_validate(values)


def test_anonymous_activity_cannot_use_modern_unrelated_exemption():
    d, _ = datum(subject_inchikey=None, original_source=None)
    assert not eligibility(d, instant_policy(), 'CANDIDATE')['eligible']
