"""Candidate-independent Human UniProt/Protein Atlas annotation acquisition."""
import argparse
import concurrent.futures
import json
from pathlib import Path

from cgr.pulsate_api.bioactivity_hypotheses import ScientificSources,tissue_relevance
from cgr.pulsate_api.functional_universe import load_universe
from cgr.pulsate_api.functional_tissue import load_tissues
from cgr.pulsate_api.virtual_organism import canonical,digest


def collect(target,root):
    reader=ScientificSources();accession=target['target_accession']
    try: result=tissue_relevance(accession,reader)
    except (ValueError,OSError) as error:result={'target_accession':accession,'status':'unsupported','reason':str(error)}
    sources=[]
    for entry in reader.audit:
        if entry.get('error'):continue
        raw=reader.payloads[entry['sha256']];path=entry['sha256']+'.json'
        (root/path).write_bytes(raw);sources.append({'path':path,'sha256':entry['sha256'],'url':entry['url']})
    print(json.dumps({'target_accession':accession,'expression_records':len(result.get('expression',[])),
        'go_records':len(result.get('go',[])),'status':result.get('status','source_replayed')}),flush=True)
    return result,sources,[e for e in reader.audit if e.get('error')]


def main():
    p=argparse.ArgumentParser();p.add_argument('--universe',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();universe,_,sha=load_universe(args.universe);args.output.mkdir(parents=True,exist_ok=False)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        rows=list(pool.map(lambda t:collect(t,args.output),universe['targets']))
    doc={'schema':'pulsate.functional-tissue-evidence/v1','universe_sha256':sha,'candidate_informed':False,
        'targets':[r[0] for r in rows],'sources':[s for r in rows for s in r[1]],'source_errors':[s for r in rows for s in r[2]],
        'scope':'Exact general Human expression/function metadata; no inferred candidate perturbation, tissue-free equivalence or clinical outcome.'}
    payload=canonical(doc);(args.output/'tissues.json').write_bytes(payload)
    load_tissues(args.output/'tissues.json',sha)
    print(json.dumps({'tissue_manifest_sha256':digest(payload),'candidate_evaluated':False}),flush=True)


if __name__=='__main__':main()
