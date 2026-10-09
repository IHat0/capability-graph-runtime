"""Denial-only offline challenge identity plus predeclared unrelated controls."""
import argparse
import json
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request,urlopen

from cgr.pulsate_api.functional_data import denial_connectivities
from cgr.pulsate_api.virtual_organism import canonical,digest


def main():
    p=argparse.ArgumentParser();p.add_argument('--protocol',type=Path,required=True)
    p.add_argument('--offline-identity',type=Path,required=True);p.add_argument('--offline-deny-name',required=True)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    protocol=json.loads(args.protocol.read_bytes())
    raw=args.offline_identity.read_bytes();doc=json.loads(raw)
    props=doc['PropertyTable']['Properties']
    if len(props)!=1: raise ValueError('Offline held-out identity is not exact.')
    identities=[];sources=[]
    def admit(name,raw,props,url,role):
        sha=digest(raw);(args.output/(sha+'.json')).write_bytes(raw)
        sources.append({'path':sha+'.json','sha256':sha,'url':url,'role':role})
        for item in props:
            smiles=item.get('SMILES') or item.get('IsomericSMILES')
            keys=denial_connectivities(smiles)
            if item['InChIKey'].split('-')[0] not in keys: raise ValueError('Denial identity graph/key mismatch.')
            identities.append({'deny_names':[name],'smiles':smiles,'inchikey':item['InChIKey'],
                'source_sha256':sha,'role':role})
    admit(args.offline_deny_name,raw,props,'offline-reviewed-identity-only','heldout_denial_only')
    for name in protocol['unrelated_acceptance']['predeclared_compounds']:
        url='https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/'+quote(name)+'/property/IsomericSMILES,InChIKey/JSON'
        with urlopen(Request(url,headers={'User-Agent':'Pulsate-general-functional-research'}),timeout=60) as r:
            raw=r.read(1024*1024+1)
        if len(raw)>1024*1024: raise ValueError('Control identity response unbounded.')
        props=json.loads(raw)['PropertyTable']['Properties']
        if not 1<=len(props)<=128: raise ValueError('Control exclusion identities unbounded.')
        admit(name,raw,props,url,'unrelated_control_excluded_from_learning')
    dossier={'purpose':'identity_denial_only','protocol_sha256':digest(args.protocol.read_bytes()),
        'identities':identities,'sources':sources,'functional_or_clinical_data_requested':False}
    payload=canonical(dossier);(args.output/'exclusions.json').write_bytes(payload)
    print(json.dumps({'sha256':digest(payload),'identity_count':len(identities),'functional_data_requested':False}))


if __name__=='__main__':main()
