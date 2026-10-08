"""General panel activity acquisition, with a candidate-exclusion firewall.

Stage one retrieves ONLY molecule identifiers. Candidate-parent records are
removed before any numerical activity request. Narrative/publication/clinical
fields are not requested. Raw numerical responses contain only admitted IDs.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import time
import urllib.parse
import urllib.request
from pathlib import Path

from cgr.pulsate_api.bioactivity_hypotheses import ACTIVITY_UNITS
from cgr.pulsate_api.safety_pharmacology import load_panel
from cgr.pulsate_api.virtual_organism import canonical, digest

BASE = 'https://www.ebi.ac.uk/chembl/api/data/'


def numerical_response_guard(doc, fields, filters):
    """Check the identity firewall before numerical response bytes are saved."""
    if 'standard_value' not in fields:
        return
    admitted = set(filters.get('molecule_chembl_id__in', '').split(',')) - {''}
    if not admitted:
        raise ValueError('Numerical acquisition requires an explicit admitted identity set')
    for row in doc['activities']:
        if (row['molecule_chembl_id'] not in admitted
                or row['target_chembl_id'] != filters['target_chembl_id']
                or row['standard_type'] != filters['standard_type']
                or row['standard_relation'] != filters['standard_relation']
                or row['assay_type'] != filters['assay_type']):
            raise ValueError('Numerical API response escaped its identity/target/assay firewall; not persisted')


class Sources:
    def __init__(self, root):
        self.root, self.audit = root, []

    def get(self, endpoint, fields, *, preserve=True, **filters):
        url = BASE + endpoint + '.json?' + urllib.parse.urlencode(dict(filters, only=','.join(fields)))
        for attempt in range(3):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={'Accept':'application/json'}),timeout=40) as response:
                    raw=response.read(8*1024*1024+1)
                if len(raw)>8*1024*1024: raise ValueError('Scientific source exceeded its bound')
                break
            except OSError:
                if attempt==2: raise
                time.sleep(1+attempt)
        doc=json.loads(raw)
        numerical_response_guard(doc, fields, filters)
        sha=digest(raw)
        if preserve:
            path=self.root/(sha+'.json')
            if not path.exists(): path.write_bytes(raw)
        self.audit.append({'url':url,'sha256':sha,'preserved':preserve,
            'scope':'Projected scientific fields; no clinical or publication narrative'})
        return doc,sha


def exclude_parent_records(sources, parents):
    excluded=set()
    for parent in parents:
        doc,_=sources.get('molecule',['molecule_chembl_id','molecule_structures'],limit=128,
            molecule_structures__standard_inchi_key__startswith=parent)
        if (doc.get('page_meta',{}).get('total_count',129)>128 or doc.get('page_meta',{}).get('next')
                or doc.get('page_meta',{}).get('total_count') != len(doc.get('molecules',[]))):
            raise ValueError('Unbounded candidate-parent exclusion lookup')
        for row in doc.get('molecules',[]):
            graph=row.get('molecule_structures') or {}
            key=graph.get('standard_inchi_key','')
            if key.split('-')[0]!=parent:
                raise ValueError('Candidate-parent identity query did not enforce its filter')
            excluded.add(row['molecule_chembl_id'])
    return excluded


def identifier_universe(sources,target,kind,excluded,bound):
    if type(bound) is not int or not 1 <= bound <= 20000:
        raise ValueError('Acquisition bound must be a positive integer at most 20000')
    identifiers=set(); offset=0
    while True:
        # No activity values, targets/mechanisms, descriptions or assay narratives
        # from an excluded molecule are preserved, displayed or passed to Pulsate.
        doc,_=sources.get('activity',['molecule_chembl_id'],preserve=False,
            target_chembl_id=target,standard_type=kind,standard_relation='=',assay_type='B',limit=1000,offset=offset)
        count=doc['page_meta']['total_count']
        if count>bound: raise ValueError('Complete endpoint dataset exceeds the predeclared acquisition bound; no truncated sample selected')
        identifiers.update(r['molecule_chembl_id'] for r in doc['activities'] if r['molecule_chembl_id'] not in excluded)
        if not doc['page_meta']['next']: break
        offset+=len(doc['activities'])
        if not doc['activities']: raise ValueError('Nonprogressing identifier pagination')
    return sorted(identifiers)


def collect_target(target,panel_sha,root,excluded,parents,kind,bound):
    output=root/(target['gene']+'-'+kind)
    output.mkdir()
    sources=Sources(output)
    result={'target':target,'panel_sha256':panel_sha,'kind':kind,'species':'Human',
        'assay_domain':'Direct single-Human-protein binding assays; heterogeneous protocols; nominal assay concentration, no functional direction',
        'excluded_parent_connectivities':parents,'excluded_identifiers':sorted(excluded),'records':[],
        'rejections':{},'numerical_candidate_activity_requested':False}
    try:
        metadata,sha=sources.get('target',['target_chembl_id','organism','target_type','target_components'],
            target_components__accession=target['target_accession'],target_type='SINGLE PROTEIN',organism='Homo sapiens',limit=4)
        matches=[r for r in metadata['targets'] if r['organism']=='Homo sapiens' and r['target_type']=='SINGLE PROTEIN'
            and len(r['target_components'])==1 and r['target_components'][0]['accession']==target['target_accession']]
        if len(matches)!=1 or metadata['page_meta']['next']: raise ValueError('No unique Human single-protein target identity')
        chembl_target=matches[0]['target_chembl_id']
        result['target_chembl_id']=chembl_target
        ids=identifier_universe(sources,chembl_target,kind,excluded,bound)
        graphs={}; assays={}
        for begin in range(0,len(ids),80):
            batch=ids[begin:begin+80]
            if set(batch)&excluded: raise ValueError('Numerical acquisition attempted an excluded compound')
            molecule_doc,molecule_sha=sources.get('molecule',['molecule_chembl_id','molecule_structures'],
                molecule_chembl_id__in=','.join(batch),limit=100)
            if molecule_doc['page_meta']['next']: raise ValueError('Unexpected molecular identity pagination')
            safe=[]
            from rdkit import Chem
            for row in molecule_doc['molecules']:
                chemical_id=row['molecule_chembl_id']; graph=row.get('molecule_structures') or {}
                if chemical_id not in batch: raise ValueError('Molecular batch query ignored its filter')
                mol=Chem.MolFromSmiles(graph.get('canonical_smiles') or '')
                if mol is None or len(Chem.GetMolFrags(mol))!=1: continue
                key=Chem.MolToInchiKey(mol)
                if key!=graph.get('standard_inchi_key'): continue
                if key.split('-')[0] in parents: raise ValueError('An excluded parent escaped identifier acquisition')
                graphs[chemical_id]={'smiles':Chem.MolToSmiles(mol,isomericSmiles=True),'inchikey':key,'source_sha256':molecule_sha}
                safe.append(chemical_id)
            if not safe: continue
            offset=0
            while True:
                doc,activity_sha=sources.get('activity',['activity_id','molecule_chembl_id','assay_chembl_id',
                    'target_chembl_id','standard_type','standard_relation','standard_value','standard_units',
                    'data_validity_comment','potential_duplicate','assay_type'],target_chembl_id=chembl_target,
                    molecule_chembl_id__in=','.join(sorted(safe)),standard_type=kind,standard_relation='=',assay_type='B',limit=1000,offset=offset)
                unknown=sorted({r['assay_chembl_id'] for r in doc['activities']} - set(assays))
                for assay_begin in range(0,len(unknown),100):
                    assay_batch=unknown[assay_begin:assay_begin+100]
                    assay_doc,assay_sha=sources.get('assay',['assay_chembl_id','confidence_score','relationship_type','target_chembl_id','assay_type'],
                        assay_chembl_id__in=','.join(assay_batch),limit=200)
                    if assay_doc['page_meta']['next']: raise ValueError('Unexpected assay metadata pagination')
                    for assay in assay_doc['assays']:
                        if assay['assay_chembl_id'] not in assay_batch: raise ValueError('Assay identity batch filter failed')
                        assays[assay['assay_chembl_id']]=(assay,assay_sha)
                    if any(a not in assays for a in assay_batch): raise ValueError('Numerical activity lacks its exact assay metadata')
                for activity in doc['activities']:
                    chemical_id=activity['molecule_chembl_id']
                    if (chemical_id not in safe or chemical_id in excluded or activity['target_chembl_id']!=chembl_target
                            or activity['assay_type']!='B'):
                        raise ValueError('Numerical API query escaped the noncandidate identity/target/assay firewall')
                    assay_id=activity['assay_chembl_id']
                    assay,assay_sha=assays[assay_id]
                    try:
                        if (assay['confidence_score']!=9 or assay['relationship_type']!='D'
                                or assay['target_chembl_id']!=chembl_target or assay['assay_type']!='B'
                                or activity['standard_type']!=kind or activity['standard_relation']!='='
                                or activity.get('data_validity_comment') or activity.get('potential_duplicate')):
                            raise ValueError('Non-direct, ambiguous, duplicate or flagged assay')
                        if activity['standard_units'] not in ACTIVITY_UNITS or isinstance(activity['standard_value'],bool):
                            raise ValueError('Unsupported concentration unit/value')
                        value_umol_l=float(activity['standard_value'])*ACTIVITY_UNITS[activity['standard_units']]
                        if not math.isfinite(value_umol_l) or value_umol_l<=0: raise ValueError('Nonpositive/nonfinite assay value')
                        result['records'].append(dict(graphs[chemical_id],molecule_chembl_id=chemical_id,
                            activity_id=activity['activity_id'],assay_chembl_id=assay_id,kind=kind,
                            value_umol_l=value_umol_l,p_activity=6-math.log10(value_umol_l),
                            activity_source_sha256=activity_sha,assay_source_sha256=assay_sha,target_source_sha256=sha))
                    except (ValueError,KeyError,TypeError) as error:
                        reason=str(error); result['rejections'][reason]=result['rejections'].get(reason,0)+1
                if not doc['page_meta']['next']: break
                offset+=len(doc['activities'])
                if not doc['activities']: raise ValueError('Nonprogressing numerical pagination')
        result['status']='acquired' if result['records'] else 'insufficient_assay_evidence'
    except (ValueError,OSError,KeyError,TypeError) as error:
        result['status']='unsupported'; result['reason']=str(error)
    result['sources']=sources.audit
    (output/'dataset.json').write_bytes(canonical(result))
    print(json.dumps({'gene':target['gene'],'kind':kind,'status':result['status'],'records':len(result['records']),
        'reason':result.get('reason'),'candidate_evaluated':False}),flush=True)
    return {'path':str((output/'dataset.json').relative_to(root)),'sha256':digest(canonical(result)),
        'target_accession':target['target_accession'],'kind':kind,'status':result['status'],'records':len(result['records'])}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--panel',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--exclude-parent',action='append',required=True)
    parser.add_argument('--kind',choices=['Ki','Kd'],default='Ki')
    parser.add_argument('--maximum-records',type=int,default=20000)
    args=parser.parse_args()
    if any(len(p)!=14 or not p.isupper() or not p.isalpha() for p in args.exclude_parent):
        raise ValueError('Excluded parents must be full InChIKey connectivity blocks')
    panel,_,panel_sha=load_panel(args.panel)
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'identity-exclusions').mkdir()
    source=Sources(args.output/'identity-exclusions')
    excluded=exclude_parent_records(source,args.exclude_parent)
    if not excluded: raise ValueError('No candidate-parent identifiers found; exclusion must be resolved before numerical acquisition')
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as workers:
        futures=[workers.submit(collect_target,target,panel_sha,args.output,excluded,args.exclude_parent,args.kind,args.maximum_records) for target in panel['targets']]
        datasets=[f.result() for f in futures]
    manifest={'schema':'pulsate.target-activity-datasets/v1','panel_sha256':panel_sha,'datasets':datasets,
        'exclusion_sources':source.audit,'excluded_parent_connectivities':args.exclude_parent,'excluded_identifiers':sorted(excluded),
        'candidate_evaluated':False,'candidate_activity_requested':False,'historical_outcome_retrieved':False,
        'limitations':['Modern unrelated-ligand binding data, not a literal historical model freeze.',
            'Binding assays do not establish functional inhibition/activation or unbound-tissue potency.']}
    (args.output/'manifest.json').write_bytes(canonical(manifest))
    print(json.dumps({'manifest_sha256':digest(canonical(manifest)),'target_count':len(datasets),
        'acquired_target_count':sum(d['status']=='acquired' for d in datasets),'candidate_evaluated':False}),flush=True)


if __name__=='__main__': main()
