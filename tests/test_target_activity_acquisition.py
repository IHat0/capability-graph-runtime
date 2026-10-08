"""Numerical firewall contract tests; no public candidate evidence is queried."""
import importlib.util
from pathlib import Path

import pytest

_spec=importlib.util.spec_from_file_location('activity_data',Path(__file__).parents[1]/'scripts'/'prepare_target_activity_data.py')
module=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(module)


class Identifiers:
    def __init__(self, *, count=3, progressing=True): self.calls=[]; self.count=count; self.progressing=progressing
    def get(self,endpoint,fields,**filters):
        self.calls.append((endpoint,fields,filters))
        offset=filters['offset']
        rows=[{'molecule_chembl_id':v} for v in (['SAFE1','EXCLUDED'] if offset==0 else ['SAFE2'])] if self.progressing else []
        return {'activities':rows,'page_meta':{'total_count':self.count,'next':'next' if offset==0 else None}},'synthetic'


def test_identifier_pass_drops_excluded_identity_without_requesting_activity_values():
    sources=Identifiers()
    assert module.identifier_universe(sources,'TARGET','Ki',{'EXCLUDED'},100)==['SAFE1','SAFE2']
    assert len(sources.calls)==2
    assert all(fields==['molecule_chembl_id'] and filters['preserve'] is False for _,fields,filters in sources.calls)
    assert all(filters['standard_type']=='Ki' for _,_,filters in sources.calls)


@pytest.mark.parametrize('bound',[0,True,20001])
def test_invalid_acquisition_bounds_refused(bound):
    with pytest.raises(ValueError): module.identifier_universe(Identifiers(),'TARGET','Ki',set(),bound)


def test_large_endpoint_not_truncated_and_empty_paging_not_looped():
    with pytest.raises(ValueError,match='no truncated sample'):
        module.identifier_universe(Identifiers(count=101),'TARGET','Ki',set(),100)
    with pytest.raises(ValueError,match='Nonprogressing'):
        module.identifier_universe(Identifiers(progressing=False),'TARGET','Ki',set(),100)


@pytest.mark.parametrize('defect',['molecule','target','kind','relation','assay','admitted'])
def test_numeric_response_must_pass_identity_and_endpoint_guard_before_persistence(defect):
    row={'molecule_chembl_id':'SAFE1','target_chembl_id':'TARGET','standard_type':'Ki','standard_relation':'=','assay_type':'B'}
    filters={'molecule_chembl_id__in':'SAFE1,SAFE2','target_chembl_id':'TARGET','standard_type':'Ki','standard_relation':'=','assay_type':'B'}
    module.numerical_response_guard({'activities':[row]},['standard_value'],filters)
    field={'molecule':'molecule_chembl_id','target':'target_chembl_id','kind':'standard_type','relation':'standard_relation','assay':'assay_type'}
    if defect=='admitted': filters.pop('molecule_chembl_id__in')
    else: row[field[defect]]='EXCLUDED'
    with pytest.raises(ValueError): module.numerical_response_guard({'activities':[row]},['standard_value'],filters)


def test_parent_exclusion_lookup_is_identity_only_and_complete():
    class Source:
        def __init__(self): self.next=None
        def get(self,endpoint,fields,**filters):
            assert endpoint=='molecule' and 'standard_value' not in fields
            return {'molecules':[{'molecule_chembl_id':'EXCLUDED','molecule_structures':{'standard_inchi_key':'ABCDEFGHIJKLMN-SYNTHETIC-N'}}],
                'page_meta':{'next':self.next,'total_count':1}},'synthetic'
    source=Source()
    assert module.exclude_parent_records(source,['ABCDEFGHIJKLMN'])=={'EXCLUDED'}
    source.next='unread-page'
    with pytest.raises(ValueError): module.exclude_parent_records(source,['ABCDEFGHIJKLMN'])
