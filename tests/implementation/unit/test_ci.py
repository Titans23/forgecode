"""Synthetic workflow/report documents test control decisions, never native claims."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import pytest
import yaml
from scripts.quality import check_dependency_graph, check_workflow, scan_secrets
from scripts.ci_run import trusted_native_dispatch
from scripts.evidence_gate import source_fingerprint, evaluate_gate
from scripts.ci_bootstrap import hydrate_node
from tests.implementation.unit.test_gates import gate_repo, write

ROOT=Path(__file__).resolve().parents[3]
PIN='11d5960a326750d5838078e36cf38b85af677262'


def workflow_file(tmp_path, *, events=None, native=False, protected=False, pinned=True):
    job={'runs-on':['self-hosted','forgecode-windows11-x64'] if native else 'ubuntu-22.04',
        'steps':[{'uses':'actions/checkout@'+(PIN if pinned else 'v4'),'with':{'persist-credentials':False}}]}
    if protected:job.update(environment='forgecode-native-acceptance',**{'if':"github.event_name == 'workflow_dispatch' && github.repository == 'Titans23/forgecode' && github.ref == 'refs/heads/main'"})
    path=tmp_path/'workflow.yml';path.write_text(yaml.safe_dump({'on':events or {'pull_request':{}},'permissions':{'contents':'read'},'jobs':{'check':job}}),encoding='utf-8')
    return path


def test_task_dependency_cycle_and_unknown_dependency_are_rejected():
    with pytest.raises(ValueError,match='cycle'):check_dependency_graph([{'id':'a','depends_on':['b']},{'id':'b','depends_on':['a']}])
    with pytest.raises(ValueError,match='Unknown'):check_dependency_graph([{'id':'a','depends_on':['missing']}])
    check_dependency_graph([{'id':'a','depends_on':[]},{'id':'b','depends_on':['a']}])


def test_real_task_cards_and_dependencies_are_acyclic():
    tasks=json.loads((ROOT/'docs/implementation/backlog.json').read_bytes())['tasks']
    check_dependency_graph(tasks)
    assert all((ROOT/'docs/implementation/tasks'/(t['id']+'.md')).is_file() for t in tasks)


@pytest.mark.parametrize('events',[{'pull_request':{}},{'pull_request_target':{}},{'workflow_run':{}}])
def test_untrusted_pr_cannot_use_native_or_secret_runner(tmp_path,events):
    with pytest.raises(ValueError):check_workflow(workflow_file(tmp_path,events=events,native=True,protected=True))


def test_only_protected_manual_native_workflow_is_accepted(tmp_path):
    check_workflow(workflow_file(tmp_path,events={'workflow_dispatch':{}},native=True,protected=True))
    with pytest.raises(ValueError,match='protected'):check_workflow(workflow_file(tmp_path,events={'workflow_dispatch':{}},native=True))


def test_floating_action_pin_is_rejected(tmp_path):
    with pytest.raises(ValueError,match='commit SHA'):check_workflow(workflow_file(tmp_path,pinned=False))


def test_repository_workflows_have_no_pr_secret_paths():
    workflows=list((ROOT/'.github/workflows').glob('*.yml'))
    assert len(workflows)>=2
    for path in workflows:check_workflow(path)
    portable=yaml.load((ROOT/'.github/workflows/portable.yml').read_text(),Loader=yaml.BaseLoader)
    assert set(portable['jobs']['portable']['strategy']['matrix']['os'])=={'ubuntu-22.04','ubuntu-24.04','windows-2025'}
    assert 'secrets.' not in (ROOT/'.github/workflows/portable.yml').read_text()
    assert any('impl.py verify --suite security' in s.get('run','') for s in portable['jobs']['dependency-security']['steps'])


def test_ci_native_runtime_guard_rejects_fork_pr_even_with_valid_labels():
    trusted={'GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_REPOSITORY':'Titans23/forgecode','GITHUB_REF':'refs/heads/main'}
    assert trusted_native_dispatch(trusted)
    for field,value in [('GITHUB_EVENT_NAME','pull_request'),('GITHUB_REPOSITORY','fork/forgecode'),('GITHUB_REF','refs/pull/1/merge')]:
        assert not trusted_native_dispatch({**trusted,field:value})


def test_secret_scanner_returns_only_location_without_secret_bytes(tmp_path):
    secret='sk-'+'A'*52
    path=tmp_path/'source.py';path.write_text("key = '"+secret+"'\n")
    findings=scan_secrets(path)
    assert len(findings)==1 and findings[0]['line']==1
    assert secret not in json.dumps(findings)


def test_private_node_hydration_rejects_changed_archive_origin(tmp_path):
    write(tmp_path/'release-lock.json',{'node':{'version':'24.21.0'},'assets':[{'name':'node','platform':'win32-x64','path':'.local/node/node.exe','sha256':'a'*64,'archive_sha256':'b'*64,'source':'https://invalid.example/runtime.zip'}]})
    with pytest.raises(ValueError,match='pinned official'):hydrate_node(tmp_path,'win32')


def fresh_records(tmp_path,monkeypatch):
    impl,original,_,_=gate_repo(tmp_path,monkeypatch)
    subprocess.run(['git','init','-q',str(tmp_path)],check=True)
    subprocess.run(['git','add','tests/test_example.py'],cwd=tmp_path,check=True)
    subprocess.run(['git','-c','user.name=Gate test','-c','user.email=gate@example.invalid','commit','-qm','fixture'],cwd=tmp_path,check=True)
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=tmp_path,text=True).strip()
    fingerprint=source_fingerprint(tmp_path);ids=[]
    for suite in ('contracts','quality','unit','portable'):
        identity='fresh-'+suite
        record={**original,'evidence_id':identity,'suite':suite,'platform':sys.platform,'git_commit':head,'source_inventory_hash':fingerprint}
        if suite in ('contracts','quality'):
            report=tmp_path/'.local/implementation'/identity/'packaging.json'
            write(report,{'status':'pass','checks':[{'name':'synthetic verdict fixture','status':'pass'}]})
            record.update(report_ref=report.relative_to(tmp_path).as_posix(),report_hash=sha256(report.read_bytes()).hexdigest())
        write(impl.DOCS/('evidence/'+identity+'.json'),record);ids.append(identity)
    return ids


def test_ci_requires_fresh_full_suite_ids_not_historical_checkout(tmp_path,monkeypatch):
    ids=fresh_records(tmp_path,monkeypatch)
    assert evaluate_gate(tmp_path,'ci',ids)['status']=='pass'
    assert evaluate_gate(tmp_path,'ci',ids[:-1])['status']=='fail'
    assert evaluate_gate(tmp_path,'ci')['status']=='fail'


def test_ci_rejects_changed_source_and_another_platform(tmp_path,monkeypatch):
    ids=fresh_records(tmp_path,monkeypatch)
    source=tmp_path/'tests/test_example.py';before=source.read_bytes();source.write_text('def test_changed(): pass\n')
    assert evaluate_gate(tmp_path,'ci',ids)['status']=='fail'
    source.write_bytes(before)
    path=tmp_path/'docs/implementation/evidence'/(''+ids[0]+'.json')
    record=json.loads(path.read_bytes());record['platform']='darwin';write(path,record)
    assert evaluate_gate(tmp_path,'ci',ids)['status']=='fail'
