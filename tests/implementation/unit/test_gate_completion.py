"""Actual report fixtures ensure known mandatory code gaps cannot become full completion."""
from tests.implementation.unit.test_gates import gate_repo,write

from scripts.evidence_gate import evaluate_gate

def test_implemented_interface_with_remaining_required_code_stays_blocked(tmp_path,monkeypatch):
    impl,record,progress,_=gate_repo(tmp_path,monkeypatch)
    progress['tasks']['F00']['remaining_implementation']=['Actual policy and full cleanup remain unimplemented']
    write(impl.DOCS/'progress.json',progress)
    report=evaluate_gate(tmp_path,'implementation')
    assert report['status']=='blocked' and report['errors']==[]
    assert report['implementation_gaps']=={'F00':progress['tasks']['F00']['remaining_implementation']}
    progress['tasks']['F00']['remaining_implementation']=[]
    write(impl.DOCS/'progress.json',progress)
    assert evaluate_gate(tmp_path,'implementation')['status']=='pass'

def test_invalid_required_code_gap_declaration_cannot_pass(tmp_path,monkeypatch):
    impl,record,progress,_=gate_repo(tmp_path,monkeypatch)
    progress['tasks']['F00']['remaining_implementation']='not a valid list'
    write(impl.DOCS/'progress.json',progress)
    assert evaluate_gate(tmp_path,'implementation')['status']=='fail'
