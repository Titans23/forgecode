"""Acceptance inventory failures cannot become native pass."""
import copy
from pathlib import Path
import pytest
from scripts.acceptance_audit import audit_mapping
ROOT=Path(__file__).resolve().parents[3]

def cases():
    return [{'id':prefix+f'{i:02d}','implementation_test_refs':['scripts/acceptance_audit.py'],
        'required_platforms':['windows'],'platform_verification':{}} for prefix,count in
        (('C',24),('W',12),('D',40),('O',10),('N',24)) for i in range(1,count+1)]

def test_complete_mapping_preserves_missing_native_proof():
    result=audit_mapping(ROOT,cases())
    assert len(result)==110
    assert all(c['platforms']['windows']['status']=='blocked' for c in result)

def test_missing_case_or_test_mapping_refuses_acceptance():
    value=cases()
    with pytest.raises(ValueError,match='86\\+24'):audit_mapping(ROOT,value[:-1])
    value[0]['implementation_test_refs']=[]
    with pytest.raises(ValueError,match='mapping missing'):audit_mapping(ROOT,value)

def test_mapping_refuses_nonexistent_symbol_and_external_file(tmp_path):
    value=cases()
    value[0]['implementation_test_refs']=['scripts/acceptance_audit.py::nonexistent_acceptance']
    with pytest.raises(ValueError,match='symbol missing'):audit_mapping(ROOT,value)
    external=tmp_path/'external.py';external.write_text('pass')
    for ref in (str(external),str(ROOT/'scripts/acceptance_audit.py'),'scripts/../scripts/acceptance_audit.py'):
        value[0]['implementation_test_refs']=[ref]
        with pytest.raises(ValueError,match='escapes'):audit_mapping(ROOT,value)
