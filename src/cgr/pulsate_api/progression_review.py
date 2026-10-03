"""Mandatory version/scope/applicability/source review for progression rules.

Numerical thresholds are deployment evidence, never production defaults. This
gate verifies the original supporting spans; it does not certify the scientific
judgment of a reviewer or turn a computational criterion into clinical safety.
"""
from .primary_measurements import locate, same_value
from .prospective_evidence import allowed_source
from .virtual_organism import digest


def reviewed_criteria(policy, sources):
    if (policy.get('schema')!='pulsate.computational-progression-policy/v2'
            or policy.get('scope_kind')!='nonclinical_computational'
            or policy.get('clinical_inference') is not False
            or policy.get('review_status') not in {'qualified','research_validated'}
            or not all(policy.get(k) for k in ('version','reviewer','reviewer_qualification','source','scope','coverage_statement'))):
        raise ValueError('Progression policy requires versioned, scientifically qualified nonclinical review.')
    rules=policy.get('functional_criteria')
    if not isinstance(rules,list) or not 1<=len(rules)<=64:
        raise ValueError('Progression cannot be authorized without explicit applicable reviewed criteria.')
    for rule in rules:
        if (rule.get('review_status')!=policy.get('review_status')
                or rule.get('evidence_level') not in {'cellular_functional','organ_physiology'}
                or rule.get('uncertainty_handling')!='all_qualified_runs_no_unresolved_domain'
                or not all(rule.get(k) for k in ('version','reviewer','reviewer_qualification','endpoint',
                    'scientific_justification','applicability_domain','provenance'))):
            raise ValueError('Criterion scope, endpoint, reviewer, domain or uncertainty review is incomplete.')
        domain=rule['applicability_domain']
        if (not isinstance(domain,dict) or not all(domain.get(k) for k in ('species','tissue','model_sha256','limitations'))
                or domain.get('model_identifier')!=rule.get('model_identifier')):
            raise ValueError('Progression criterion is not qualified for an exact model revision/context.')
        if policy['review_status']=='research_validated':
            from .research_qualification import research_review
            research_review(policy,sources,model_sha256=domain['model_sha256'])
            research_review(rule,sources,model_sha256=domain['model_sha256'])
        provenance=rule['provenance']
        raw=(sources or {}).get(provenance.get('source_sha256'))
        if (raw is None or len(raw)>16*1024*1024 or digest(raw)!=provenance.get('source_sha256')
                or not allowed_source(provenance.get('source_url',''))
                or provenance.get('compound_specific') is not False
                or not provenance.get('record_identifier') or not provenance.get('supporting_spans')):
            raise ValueError('Criterion has no preserved general scientific provenance; no default threshold is invented.')
        for span in provenance['supporting_spans']:
            actual=locate(raw,provenance.get('source_format','json'),span['pointer'],
                pdf_reviews=provenance.get('pdf_reviews',()),sources=sources)
            if not same_value(actual,span['expected']): raise ValueError('Progression criterion original-source replay failed.')
    return rules
