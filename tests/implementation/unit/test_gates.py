"""Real report files exercise gate decisions; no simulated native acceptance."""
from hashlib import sha256
import json
from pathlib import Path
import pytest
from tests.implementation.unit.test_impl_audit import load_impl


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def gate_repo(tmp_path, monkeypatch, *, xml='<testcase classname="test_example" name="actual"/>', declared='pass', exit_code=0):
    impl = load_impl()
    monkeypatch.setattr(impl, 'ROOT', tmp_path)
    monkeypatch.setattr(impl, 'DOCS', tmp_path/'docs/implementation')
    report = tmp_path/'.local/implementation/first/junit.xml'
    report.parent.mkdir(parents=True)
    report.write_text('<testsuites><testsuite>'+xml+'</testsuite></testsuites>')
    record = {'schema_version':'forge.implementation.evidence.v1','evidence_id':'first','task_id':'F00',
        'suite':'unit','case_ids':['N23'],'git_commit':'a'*40,'dirty_hash':'b'*64,
        'platform':'win32','os_build':'Windows-10-10.0.19045-SP0','start':'2026-10-07T00:00:00+00:00',
        'end':'2026-10-07T00:01:00+00:00','command':['python','-m','pytest'],'exit_code':exit_code,
        'report_ref':report.relative_to(tmp_path).as_posix(),'report_hash':sha256(report.read_bytes()).hexdigest(),
        'status':declared,'tests':{'collected':1,'skipped':0,'failures':0,'errors':0}}
    write(impl.DOCS/'evidence/first.json', record)
    progress = {'tasks':{'F00':{'implementation_status':'implemented','verification':{'unit':declared},
        'evidence_ids':['first'],'blocked_reasons':[]}}}
    write(impl.DOCS/'progress.json', progress)
    write(impl.DOCS/'backlog.json', {'tasks':[{'id':'F00','depends_on':[],'verification_suites':['unit']}]})
    case = {'id':'N23','owner_task':'F00','status':'pass','release_required':True,
        'implementation_test_refs':['tests/test_example.py'],'required_platforms':['portable'],
        'platform_verification':{'portable':{'status':'pass','evidence_ids':['first']}}}
    write(impl.DOCS/'acceptance-registry.json', {'cases':[case]})
    (tmp_path/'tests').mkdir(); (tmp_path/'tests/test_example.py').write_text('def test_actual(): pass\n')
    write(tmp_path/'release-lock.json', {'resolution_status':'resolved','security':{'status':'pass'}})
    write(tmp_path/'release-manifest.json', {'channel':'production','project_license_status':'present','signature':{'status':'verified'}})
    return impl, record, progress, case


def test_declared_pass_cannot_hide_zero_actual_tests(tmp_path, monkeypatch):
    impl, *_ = gate_repo(tmp_path, monkeypatch, xml='')
    assert impl.gate('implementation')['status']=='fail'


@pytest.mark.parametrize('xml', ['<testcase><skipped/></testcase>', '<testcase><failure/></testcase>', '<testcase><error/></testcase>'])
def test_declared_pass_cannot_hide_required_skip_or_failure(tmp_path, monkeypatch, xml):
    impl, *_ = gate_repo(tmp_path, monkeypatch, xml=xml)
    assert impl.gate('implementation')['status']!='pass'


def test_missing_supported_windows_machine_blocks_release(tmp_path, monkeypatch):
    impl, record, progress, case = gate_repo(tmp_path, monkeypatch)
    case.update(required_platforms=['windows'], platform_verification={'windows':{'status':'not_run','evidence_ids':[]}})
    write(impl.DOCS/'acceptance-registry.json', {'cases':[case]})
    assert impl.gate('release')['status']!='pass'


def test_changed_report_cannot_attest_to_pass(tmp_path, monkeypatch):
    impl, record, *_ = gate_repo(tmp_path, monkeypatch)
    (tmp_path/record['report_ref']).write_text('<testsuites/>')
    assert impl.gate('implementation')['status']=='fail'


def test_missing_report_cannot_attest_to_pass(tmp_path, monkeypatch):
    impl, record, *_ = gate_repo(tmp_path, monkeypatch)
    (tmp_path/record['report_ref']).unlink()
    assert impl.gate('implementation')['status']=='fail'


def test_latest_success_supersedes_historical_failure_without_erasing_it(tmp_path, monkeypatch):
    impl, old, progress, _ = gate_repo(tmp_path, monkeypatch, xml='<testcase><failure/></testcase>', declared='fail', exit_code=1)
    report=tmp_path/'.local/implementation/second/junit.xml';report.parent.mkdir()
    report.write_text('<testsuites><testsuite><testcase name="actual"/></testsuite></testsuites>')
    new={**old,'evidence_id':'second','start':'2026-10-07T00:02:00+00:00','end':'2026-10-07T00:03:00+00:00',
        'exit_code':0,'status':'pass','report_ref':report.relative_to(tmp_path).as_posix(),'report_hash':sha256(report.read_bytes()).hexdigest()}
    write(impl.DOCS/'evidence/second.json',new)
    progress['tasks']['F00'].update(evidence_ids=['first','second'],verification={'unit':'pass'})
    write(impl.DOCS/'progress.json',progress)
    assert impl.gate('implementation')['status']=='pass'
    assert (impl.DOCS/'evidence/first.json').is_file()


def test_latest_failure_is_never_replaced_by_older_success(tmp_path, monkeypatch):
    impl, old, progress, _=gate_repo(tmp_path,monkeypatch)
    new={**old,'evidence_id':'second','start':'2026-10-07T00:02:00+00:00','status':'fail','exit_code':1}
    write(impl.DOCS/'evidence/second.json',new)
    progress['tasks']['F00']['evidence_ids'].append('second');write(impl.DOCS/'progress.json',progress)
    assert impl.gate('implementation')['status']=='fail'


def test_junit_counts_are_recomputed_not_trusted(tmp_path,monkeypatch):
    impl,record,*_=gate_repo(tmp_path,monkeypatch)
    record['tests']['collected']=2000
    write(impl.DOCS/'evidence/first.json',record)
    assert impl.gate('implementation')['status']=='fail'


def test_registered_suite_rejects_wrong_report_format(tmp_path,monkeypatch):
    impl,record,*_=gate_repo(tmp_path,monkeypatch)
    record['suite']='quality';write(impl.DOCS/'evidence/first.json',record)
    assert impl.gate('implementation')['status']=='fail'


def test_owned_report_path_cannot_escape_repository(tmp_path,monkeypatch):
    impl,record,*_=gate_repo(tmp_path,monkeypatch)
    record['report_ref']='../outside.xml';write(impl.DOCS/'evidence/first.json',record)
    assert impl.gate('implementation')['status']=='fail'


def test_json_native_declaration_needs_actual_eligible_checks(tmp_path,monkeypatch):
    impl,record,*_=gate_repo(tmp_path,monkeypatch)
    report=tmp_path/'.local/implementation/first/native.json'
    write(report,{'status':'pass','checks':[{'status':'pass'}],'eligible_for_native_pass':False})
    record.update(suite='sandbox-windows',report_ref=report.relative_to(tmp_path).as_posix(),report_hash=sha256(report.read_bytes()).hexdigest())
    write(impl.DOCS/'evidence/first.json',record)
    assert impl.gate('implementation')['status']=='fail'


def test_unknown_json_verdict_cannot_establish_pass(tmp_path,monkeypatch):
    impl,record,*_=gate_repo(tmp_path,monkeypatch)
    report=tmp_path/'.local/implementation/first/quality.json'
    write(report,{'status':'unknown','checks':[{'status':'pass'}]})
    record.update(suite='quality',status='unknown',report_ref=report.relative_to(tmp_path).as_posix(),report_hash=sha256(report.read_bytes()).hexdigest())
    write(impl.DOCS/'evidence/first.json',record)
    assert impl.gate('implementation')['status']=='fail'
