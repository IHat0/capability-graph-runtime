"""Source-bound general Human tissue annotations, not candidate effects."""
from __future__ import annotations

import json
from pathlib import Path

from .bioactivity_hypotheses import ScientificSources,tissue_relevance
from .virtual_organism import canonical,digest


def load_tissues(path,universe_sha):
    path=Path(path).resolve(strict=True);payload=path.read_bytes();doc=json.loads(payload)
    if (len(payload)>4*1024*1024 or doc.get('schema')!='pulsate.functional-tissue-evidence/v1'
            or doc.get('universe_sha256')!=universe_sha or doc.get('candidate_informed') is not False
            or len(doc['targets'])>128):raise ValueError('Tissue evidence does not bind the frozen general universe.')
    sources={digest(payload):payload};preserved={}
    for entry in doc['sources']:
        file=(path.parent/entry['path']).resolve(strict=True)
        if not file.is_relative_to(path.parent) or file.stat().st_size>4*1024*1024:raise ValueError('Tissue source outside trusted path/bound.')
        raw=file.read_bytes()
        if digest(raw)!=entry['sha256']:raise ValueError('Tissue source hash changed.')
        sources[entry['sha256']]=raw;preserved[entry['url']]=raw
    for error in doc.get('source_errors',[]):preserved[error['url']]=error
    reader=ScientificSources(preserved=preserved)
    seen=set()
    for target in doc['targets']:
        accession=target['target_accession']
        if accession in seen:raise ValueError('Duplicate tissue target.')
        seen.add(accession)
        try: expected=tissue_relevance(accession,reader)
        except ValueError as error:expected={'target_accession':accession,'status':'unsupported','reason':str(error)}
        if canonical(expected)!=canonical(target):raise ValueError('Human tissue/function evidence failed primary-source replay.')
        if target.get('taxonomy_id') not in (None,9606):raise ValueError('Non-Human tissue annotation.')
    return doc,sources,digest(payload)


def annotate_predictions(prediction,tissues):
    if prediction is None:return None
    evidence={t['target_accession']:t for t in tissues['targets']}
    # Every result carries its exact annotation even when activity is refused.
    # No expression-derived potency, toxicity score or organ mapping is invented.
    return {'schema':'pulsate.functional-tissue-prioritization/v1',
        'universe_sha256':prediction['universe_sha256'],
        'targets':[{'target_accession':r['target_accession'],'action':r.get('action'),
            'activity_status':r['status'],'tissue_evidence':evidence.get(r['target_accession']),
            'priority_status':'requires_compatible_unbound_exposure' if r['status']=='predicted' else 'unsupported_functional_activity',
            'limitation':'General expression/function can prioritize a compatible mechanism; it is not a candidate effect or automatic physiological parameter.'}
            for r in prediction['targets']]}
