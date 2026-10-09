"""Predeclared research concern gates; never a toxicity or rejection verdict.

Rules must be reviewed general mechanisms with exact primary-source anchors.
No rules are generated from the candidate or its observed result. Exploratory
physiology cannot substitute for missing molecular evidence or uncertainty.
"""
from __future__ import annotations

from .functional_pharmacology import _anchor
from .virtual_organism import canonical,digest

GATES=('supported_human_exposure','supported_functional_action_potency','compatible_overlap',
    'source_bound_tissue_relevance','predeclared_undesirable_mechanism','robust_to_uncertainty','blocking_verification')


def assess(candidate,criteria,sources,*,verification_passed=False):
    default={'status':'INSUFFICIENT EVIDENCE','scope':'Exposure-relevant research concern only, not toxicity proof or clinical validity.',
        'reject_available':False,'matched_rules':[],'rule_results':[], 'criteria_sha256':None}
    if not criteria:return dict(default,reason='No source-reviewed general undesirable-mechanism criteria configured; no candidate-specific rule is invented.')
    if (criteria.get('schema')!='pulsate.functional-concern-criteria/v1' or criteria.get('candidate_informed') is not False
            or criteria.get('reject_available') is not False or not all(criteria.get(k) for k in ('reviewer','version','scope','limitations'))
            or not 1<=len(criteria.get('rules',[]))<=128):raise ValueError('Functional concern requires bounded predeclared candidate-independent research rules.')
    results=[];matched=[]
    for rule in criteria['rules']:
        if (not all(rule.get(k) for k in ('identifier','target_accession','action','kind','organ','compartment','undesirable_mechanism','source_anchors','tissue_anchors','limitations'))
                or rule['kind'] not in ('IC50','EC50') or rule['action'] not in ('blocker','inhibitor','agonist','antagonist')):
            raise ValueError('Incomplete general functional concern rule; no defaults.')
        for anchor in (*rule['source_anchors'],*rule['tissue_anchors']):_anchor(anchor,sources)
        evidence=[a for a in candidate['quantitative_activity'] if a.get('target_accession')==rule['target_accession']
            and a.get('functional_direction')==rule['action'] and a.get('kind')==rule['kind']
            and a.get('species')=='Human' and a.get('eligibility',{}).get('eligible') is True]
        gates={key:False for key in GATES};gates['predeclared_undesirable_mechanism']=True
        gates['source_bound_tissue_relevance']=True;gates['blocking_verification']=verification_passed
        for activity in evidence:
            gates['supported_functional_action_potency']=True
            if activity.get('concentration_basis')!='unbound' or activity.get('organ')!=rule['organ'] or activity.get('compartment')!=rule['compartment']:continue
            comparisons=[o for o in candidate['exposure_activity'] if o.get('activity_sha256')==digest(canonical(activity))]
            for overlap in comparisons:
                pairs=overlap.get('comparisons',[])
                native=bool(pairs) and all(p.get('native_population_sha256') and p.get('subject_identifier') and p.get('native_path') for p in pairs)
                gates['supported_human_exposure']|=native
                gates['compatible_overlap']|=native and any(p['peak_activity_ratio_interval'][1]>=1 for p in pairs)
                sensitivity=candidate.get('native_sensitivity')
                complete=(not sensitivity or sensitivity.get('summary',{}).get('complete_discrete_space') is True)
                # Point measurements without quantified uncertainty cannot pass
                # robustness. Require even the upper potency bound to overlap
                # every supplied valid population/scenario peak.
                robust=native and complete and all(p['peak_activity_ratio_interval'][0]>=1 for p in pairs)
                gates['robust_to_uncertainty']|=robust
        passed=all(gates.values())
        results.append({'identifier':rule['identifier'],'gates':gates,'passed':passed,
            'missing_gates':[g for g,v in gates.items() if not v],'source_rule_sha256':digest(canonical(rule))})
        if passed:matched.append(rule['identifier'])
    return dict(default,status='CONCERN' if matched else 'INSUFFICIENT EVIDENCE',matched_rules=matched,
        rule_results=results,criteria_sha256=digest(canonical(criteria)),
        reason='Source-bound functional mechanism overlaps supported exposure under all disclosed uncertainty cases; investigate, not a toxicity conclusion.' if matched else
            'At least one critical functional concern gate is missing. No negative safety conclusion or REJECT is available.')
