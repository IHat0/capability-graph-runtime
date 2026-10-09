"""Complete bounded functional datasets, admitted identities before labels.

No clinical literature is requested. The exclusion file supplies only offline
identity graphs and names for denial, never a candidate activity search.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from cgr.pulsate_api.bioactivity_hypotheses import ACTIVITY_UNITS
from cgr.pulsate_api.functional_universe import load_universe
from cgr.pulsate_api.functional_data import denial_connectivities, elemental_inventory, graph_allowed, assay_semantics, numeric_guard, stratum_key, stratum_identifier
from cgr.pulsate_api.virtual_organism import canonical, digest

BASE = 'https://www.ebi.ac.uk/chembl/api/data/'
ACTIVITY_FIELDS = ['activity_id','molecule_chembl_id','assay_chembl_id','target_chembl_id',
    'standard_type','standard_relation','standard_units','standard_value','data_validity_comment','potential_duplicate']
ASSAY_FIELDS = ['assay_chembl_id','target_chembl_id','assay_type','assay_organism','confidence_score',
    'relationship_type','description','assay_cell_type','bao_format']


class Sources:
    def __init__(self, root, excluded_ids=(), denied_names=(), cached=None, *, allowed_relations=('=',)):
        self.root, self.audit = root, []
        self.excluded_ids, self.denied_names = set(excluded_ids), [n.casefold() for n in denied_names if n]
        # Censored bounds are allowed only in an explicit isolated source audit.
        # Equality-only training acquisition and source replay are unchanged.
        self.allowed_relations=tuple(allowed_relations)
        self.cached={}
        if cached and (cached/'dataset.json').exists():
            for item in json.loads((cached/'dataset.json').read_bytes())['sources']:
                if item.get('preserved') and not item.get('quarantined'):
                    self.cached[item['url']]=(cached/(item['sha256']+'.json'),item['sha256'])

    def get(self, endpoint, fields, *, preserve=True, **filters):
        url = BASE + endpoint + '.json?' + urlencode(dict(filters, only=','.join(fields)))
        cached=self.cached.get(url)
        for attempt in range(3):
            try:
                if cached:
                    raw=cached[0].read_bytes()
                    if digest(raw)!=cached[1]:raise ValueError('Cached original source changed.')
                else:
                    with urlopen(Request(url, headers={'Accept':'application/json'}), timeout=45) as response:
                        raw = response.read(8*1024*1024+1)
                if len(raw)>8*1024*1024:
                    raise ValueError('Functional source exceeds complete response bound.')
                break
            except OSError:
                if attempt==2: raise
                time.sleep(1+attempt)
        document = json.loads(raw)
        if 'standard_value' in fields:
            numeric_guard(document, filters, self.excluded_ids,allowed_relations=self.allowed_relations)
        if endpoint=='assay' and any(name in raw.decode().casefold() for name in self.denied_names):
            self.audit.append({'url':url,'sha256':digest(raw),'preserved':False,'quarantined':True,
                'reason':'Excluded identity name in assay metadata; no label request follows.'})
            raise ValueError('Excluded identity in assay metadata; entire response quarantined before labels.')
        sha = digest(raw)
        if preserve and not (self.root/(sha+'.json')).exists():
            (self.root/(sha+'.json')).write_bytes(raw)
        self.audit.append({'url':url,'sha256':sha,'preserved':preserve,
            'scope':'Projected target/graph/assay or admitted numerical fields; no clinical/outcome narrative'})
        return document, sha

    def complete(self, endpoint, fields, bound=20000, preserve=True, **filters):
        key={'target':'targets','assay':'assays','molecule':'molecules','activity':'activities'}[endpoint]
        rows, offset = [], 0
        while True:
            document, sha = self.get(endpoint, fields, preserve=preserve,limit=1000,offset=offset,**filters)
            meta=document['page_meta']
            if meta['total_count']>bound:
                raise ValueError('Complete endpoint exceeds predeclared bound; no truncated sample.')
            rows.extend((row,sha) for row in document[key])
            if not meta['next']:
                if len(rows)!=meta['total_count']: raise ValueError('Incomplete endpoint pagination.')
                return rows
            if not document[key]: raise ValueError('Nonprogressing pagination.')
            offset+=len(document[key])


def collect(target, universe_sha, output, excluded, denied, inventories, names, bound, cached=None):
    root=output/target['target_accession']; root.mkdir()
    sources=Sources(root,excluded,names,cached/target['target_accession'] if cached else None)
    result={'schema':'pulsate.functional-target-data/v1','target':target,'universe_sha256':universe_sha,
        'records':[],'strata':{},'excluded_identifiers':sorted(excluded),
        'excluded_connectivities':sorted(denied),'candidate_activity_requested':False,
        'excluded_element_inventories':inventories,
        'rejections':{},'status':'unsupported'}
    rejected=Counter()
    try:
        matches=sources.complete('target',['target_chembl_id','organism','target_type','target_components'],bound=8,
            target_components__accession=target['target_accession'],target_type='SINGLE PROTEIN',organism='Homo sapiens')
        matches=[(r,s) for r,s in matches if r['organism']=='Homo sapiens' and r['target_type']=='SINGLE PROTEIN'
            and len(r['target_components'])==1 and r['target_components'][0]['accession']==target['target_accession']]
        if len(matches)!=1: raise ValueError('No unique direct single Human protein identity.')
        target_row,target_sha=matches[0]; chembl=target_row['target_chembl_id']
        result['target_chembl_id']=chembl
        for endpoint in ('IC50','EC50'):
            endpoint_start=len(result['records'])
            try:
                ids=sources.complete('activity',['molecule_chembl_id','assay_chembl_id'],bound=bound,preserve=False,
                    target_chembl_id=chembl,standard_type=endpoint,standard_relation='=')
                pairs={(r['molecule_chembl_id'],r['assay_chembl_id']) for r,_ in ids if r['molecule_chembl_id'] not in excluded}
                graphs={}; semantic={}
                molecules=sorted({m for m,a in pairs})
                for start in range(0,len(molecules),80):
                    batch=molecules[start:start+80]
                    for row,sha in sources.complete('molecule',['molecule_chembl_id','molecule_structures'],bound=100,
                            molecule_chembl_id__in=','.join(batch)):
                        if row['molecule_chembl_id'] not in batch: raise ValueError('Molecule identity filter escaped.')
                        try:
                            structure=row.get('molecule_structures') or {}
                            smiles=graph_allowed(structure,denied,inventories)
                            graphs[row['molecule_chembl_id']]={'smiles':smiles,'inchikey':structure['standard_inchi_key'],'graph_source_sha256':sha}
                        except ValueError as error: rejected[str(error)]+=1
                assay_ids=sorted({a for m,a in pairs if m in graphs})
                for start in range(0,len(assay_ids),40):
                    batch=assay_ids[start:start+40]
                    try:
                        rows=sources.complete('assay',ASSAY_FIELDS,bound=100,assay_chembl_id__in=','.join(batch))
                    except ValueError as error:
                        rejected[str(error)]+=len(batch); continue
                    for row,sha in rows:
                        if row['assay_chembl_id'] not in batch or row['target_chembl_id']!=chembl:
                            raise ValueError('Assay identity filter escaped.')
                        try:
                            sem=assay_semantics(row,target['families'])
                            key=stratum_key(target['target_accession'],endpoint,sem)
                            identifier=stratum_identifier(key)
                            result['strata'][identifier]=key
                            semantic[row['assay_chembl_id']]=(sem,sha,identifier)
                        except ValueError as error: rejected[str(error)]+=1
                for start in range(0,len(molecules),60):
                    batch=[m for m in molecules[start:start+60] if m in graphs]
                    if not batch: continue
                    safe_assays=sorted({a for m,a in pairs if m in batch and a in semantic})
                    # Avoid selecting any unsafe assay by specifying both sets.
                    for begin in range(0,len(safe_assays),60):
                        assay_batch=safe_assays[begin:begin+60]
                        if not any((m,a) in pairs for m in batch for a in assay_batch): continue
                        rows=sources.complete('activity',ACTIVITY_FIELDS,bound=bound,
                            target_chembl_id=chembl,standard_type=endpoint,standard_relation='=',
                            molecule_chembl_id__in=','.join(batch),assay_chembl_id__in=','.join(assay_batch))
                        for row,sha in rows:
                            try:
                                if row.get('data_validity_comment') or row.get('potential_duplicate'): raise ValueError('Flagged/duplicate assay value.')
                                if row['standard_units'] not in ACTIVITY_UNITS or isinstance(row['standard_value'],bool): raise ValueError('Unsupported concentration units/value.')
                                value=float(row['standard_value'])*ACTIVITY_UNITS[row['standard_units']]
                                if not math.isfinite(value) or value<=0: raise ValueError('Nonpositive/nonfinite functional potency.')
                                sem,assay_sha,stratum=semantic[row['assay_chembl_id']]
                                result['records'].append(dict(graphs[row['molecule_chembl_id']],
                                    molecule_chembl_id=row['molecule_chembl_id'],assay_chembl_id=row['assay_chembl_id'],
                                    activity_id=row['activity_id'],kind=endpoint,value_umol_l=value,p_activity=6-math.log10(value),
                                    semantics=sem,stratum=stratum,assay_source_sha256=assay_sha,
                                    activity_source_sha256=sha,target_source_sha256=target_sha))
                            except (ValueError,KeyError,TypeError) as error: rejected[str(error)]+=1
            except (ValueError,OSError,KeyError,TypeError) as error:
                # Never fit a partial endpoint after a later page/network failure.
                del result['records'][endpoint_start:]
                rejected[endpoint+': '+str(error)]+=1
        result['status']='acquired' if result['records'] else 'insufficient_functional_evidence'
    except (ValueError,OSError,KeyError,TypeError) as error:
        result['reason']=str(error)
    result['rejections']=dict(rejected); result['sources']=sources.audit
    payload=canonical(result); (root/'dataset.json').write_bytes(payload)
    receipt={'path':target['target_accession']+'/dataset.json','sha256':digest(payload),
        'target_accession':target['target_accession'],'status':result['status'],'records':len(result['records']),
        'strata':len(result['strata']),'reason':result.get('reason')}
    print(json.dumps(receipt),flush=True)
    return receipt


def main():
    p=argparse.ArgumentParser(); p.add_argument('--universe',type=Path,required=True)
    p.add_argument('--exclusions',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    p.add_argument('--cached-attempt',type=Path)
    args=p.parse_args(); universe,_,universe_sha=load_universe(args.universe)
    protocol=json.loads((args.universe.parent/'protocol.json').read_bytes())
    exclusion_bytes=args.exclusions.read_bytes(); exclusion=json.loads(exclusion_bytes)
    if exclusion.get('purpose')!='identity_denial_only' or not exclusion.get('identities'):
        raise ValueError('Offline identity-only exclusion dossier required before labels.')
    denied=set(); names=[]; inventories=set()
    for identity in exclusion['identities']:
        denied.update(denial_connectivities(identity['smiles']))
        # Exclude every control's exact graph/labels from learning. Standard
        # unrelated assay challenge/comparator names are not held-out outcome
        # leakage; quarantining them would incorrectly discard entire assays.
        if identity['role']=='heldout_denial_only': names.extend(identity.get('deny_names',[]))
        elif identity['role']!='unrelated_control_excluded_from_learning':
            raise ValueError('Unknown exclusion role; no labels may be requested.')
        inventories.update(elemental_inventory(identity['smiles']))
    inventories=sorted(inventories)
    args.output.mkdir(parents=True,exist_ok=False); (args.output/'identity-exclusions').mkdir()
    source=Sources(args.output/'identity-exclusions')
    excluded=set()
    for parent in sorted(denied):
        for row,_ in source.complete('molecule',['molecule_chembl_id','molecule_structures'],bound=128,
                molecule_structures__standard_inchi_key__startswith=parent):
            structure=row.get('molecule_structures') or {}
            if structure.get('standard_inchi_key','').split('-')[0]!=parent: raise ValueError('Exclusion identity filter escaped.')
            excluded.add(row['molecule_chembl_id'])
    (args.output/'exclusions.json').write_bytes(exclusion_bytes)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        jobs=[pool.submit(collect,t,universe_sha,args.output,excluded,denied,inventories,names,
            protocol['functional_data']['maximum_records_per_endpoint'],args.cached_attempt) for t in universe['targets']]
        datasets=[f.result() for f in jobs]
    manifest={'schema':'pulsate.functional-datasets/v1','universe_sha256':universe_sha,
        'protocol_sha256':digest(canonical(protocol)),'exclusions_sha256':digest(exclusion_bytes),
        'excluded_connectivities':sorted(denied),'excluded_identifiers':sorted(excluded),
        'excluded_element_inventories':inventories,
        'exclusion_sources':source.audit,'datasets':datasets,'candidate_activity_requested':False,
        'candidate_evaluated':False,'historical_outcome_retrieved':False}
    (args.output/'manifest.json').write_bytes(canonical(manifest))
    print(json.dumps({'manifest_sha256':digest(canonical(manifest)),'datasets':len(datasets)}),flush=True)


if __name__=='__main__': main()
