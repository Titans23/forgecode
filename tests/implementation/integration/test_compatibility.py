"""F26 compatibility runs actual CLI/Engine Harness paths and preserves source data."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from forge.cli import create_session_runtime
from forge.config import ForgeConfig
from forge.runtime.dependencies import RuntimeBindings
from test_application import ScriptedClient, script, setup

ROOT=Path(__file__).resolve().parents[3]


@pytest.mark.parametrize('command',['engine','sandbox','web'])
def test_new_cli_commands_delegate_to_real_module_help(command):
    result=subprocess.run([sys.executable,'-m','forge.cli',command,'--help'],cwd=ROOT,capture_output=True,text=True,timeout=20)
    assert result.returncode==0 and 'usage:' in result.stdout.lower()


def test_cli_eval_catalog_matches_existing_actual_catalog():
    result=subprocess.run([sys.executable,'-m','forge.cli','eval','list','--json'],cwd=ROOT,capture_output=True,text=True,timeout=20)
    from benchmark.catalog import BENCHMARKS
    assert result.returncode==0 and json.loads(result.stdout)==json.loads(json.dumps([spec.to_dict() for spec in BENCHMARKS]))


def test_cli_catalog_json_preserves_unicode_in_ascii_stdout():
    environment={**os.environ,'PYTHONIOENCODING':'ascii','PYTHONUTF8':'0'}
    result=subprocess.run([sys.executable,'-m','forge.cli','eval','list','--json'],cwd=ROOT,
        env=environment,capture_output=True,text=True,encoding='ascii',timeout=20)
    from benchmark.catalog import BENCHMARKS
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)==json.loads(json.dumps([spec.to_dict() for spec in BENCHMARKS]))


def test_web_cli_rejects_public_bind_before_creating_data_or_listener(tmp_path):
    data=tmp_path/'private-data'
    result=subprocess.run([sys.executable,'-m','forge.cli','web','--data-dir',str(data),'--host','0.0.0.0'],
        cwd=ROOT,capture_output=True,text=True,timeout=20)
    assert result.returncode==2 and 'unrecognized arguments' in result.stderr and not data.exists()


@pytest.mark.parametrize('kind',['mcp','hooks','channels','permissions'])
def test_unknown_legacy_config_fields_warn_without_disclosing_values(tmp_path,kind,monkeypatch):
    root=tmp_path/'project';root.mkdir()
    if kind=='mcp':
        from forge.mcp.config import load_mcp_servers
        path=root/'.mcp.json';known='mcpServers'
        load=lambda:load_mcp_servers(root,user_path=tmp_path/'unused-user.json')
    elif kind=='hooks':
        from forge.hooks.config import load_hook_settings
        path=root/'.forge/settings.json';path.parent.mkdir();known='hooks'
        load=lambda:load_hook_settings(root,user_settings_path=tmp_path/'unused-user.json')
    elif kind=='channels':
        from forge.channels.config import load_channel_settings, _FEISHU_ENV_FIELDS
        for name in _FEISHU_ENV_FIELDS:monkeypatch.delenv(name,raising=False)
        path=root/'.forge/channels.json';path.parent.mkdir();known='channels'
        load=lambda:load_channel_settings(root,user_path=tmp_path/'unused-user.json')
    else:
        from forge.permissions.policy import PermissionManager
        path=root/'.forge/permissions.json';path.parent.mkdir();known='rules'
        load=lambda:PermissionManager(root,user_path=tmp_path/'unused-user.json')
    path.write_text(json.dumps({known:[] if kind=='permissions' else {},'futureSetting':'do-not-disclose-secret-value'}),encoding='utf-8')
    before=path.read_bytes()
    with pytest.warns(UserWarning,match='futureSetting') as warnings:
        load()
    assert 'do-not-disclose-secret-value' not in str(warnings[0].message)
    assert 'supported' in str(warnings[0].message).lower() and path.read_bytes()==before


def test_actual_project_permission_grant_preserves_unknown_root_and_rule_values(tmp_path):
    from forge.permissions.policy import ApprovalResponse, PermissionManager, PermissionRequest
    root=tmp_path/'project';path=root/'.forge/permissions.json';path.parent.mkdir(parents=True)
    original={'version':1,'futureSetting':{'keep':'unknown-value'},'rules':[
        {'action':'ask','capability':'file.write','target':'*','futureRule':{'keep':'rule-value'}}]}
    path.write_text(json.dumps(original),encoding='utf-8')
    async def approve(request):return ApprovalResponse('allow_project')
    with pytest.warns(UserWarning,match='futureSetting'):
        manager=PermissionManager(root,mode='supervised',approval_handler=approve,user_path=tmp_path/'unused')
    decision=asyncio.run(manager.authorize(PermissionRequest('edit_file','file.write','medium',('new.txt',))))
    after=json.loads(path.read_text())
    assert decision.action=='allow' and after['futureSetting']==original['futureSetting'] and after['version']==1
    assert after['rules'][0]['futureRule']==original['rules'][0]['futureRule'] and len(after['rules'])==2


def test_permission_grant_refuses_to_overwrite_malformed_previous_config(tmp_path):
    from forge.permissions.policy import PermissionManager, PermissionRule
    path=tmp_path/'permissions.json';path.write_bytes(b'{damaged original')
    with pytest.raises(ValueError,match='preserved'):
        PermissionManager._save_rules(path,[PermissionRule('allow')])
    assert path.read_bytes()==b'{damaged original'


def test_actual_cli_runtime_owns_same_project_lock_as_engine_without_model_overlap(tmp_path):
    service,store,_,clients,_,turn=setup(tmp_path)
    code="""
import asyncio,sys
from pathlib import Path
from forge.cli import create_session_runtime
from forge.config import ForgeConfig
from forge.runtime.dependencies import RuntimeBindings
from forge.runtime.state import ModelTextDelta
class WaitingClient:
    provider='anthropic';model='scripted-test';max_tokens=1024
    async def stream(self,messages,tools=None,system=None):
        print('cli-model-active',flush=True)
        await asyncio.sleep(60)
        yield ModelTextDelta('offline fixture')
    async def aclose(self):pass
async def run():
    runtime,_,_=create_session_runtime(Path(sys.argv[1]),bindings=RuntimeBindings(
        config=ForgeConfig(api_key='offline-fixture',model_id='scripted-test',max_tokens=1024),
        model_client_factory=lambda config,**kwargs:WaitingClient(),data_root=Path(sys.argv[2]),
        trusted_extensions=False,task_relation='new',max_model_calls=1,max_tool_calls=1,wall_seconds=60))
    try:
        async for event in runtime.stream('Read the value.'):pass
    finally:await runtime.runtime_close()
asyncio.run(run())
"""
    peer=subprocess.Popen([sys.executable,'-c',code,str(tmp_path/'project'),str(tmp_path/'cli-data')],
        cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
    try:
        assert peer.stdout.readline().strip()=='cli-model-active'
        accepted=service.start_turn(turn)
        asyncio.run(service.execute_turn(accepted['turn_id']))
        row=store.connection.execute('SELECT outcome FROM turns WHERE id=?',(accepted['turn_id'],)).fetchone()
        assert row[0]=='blocked' and all(not client.calls for client in clients)
        assert (tmp_path/'project/value.txt').read_text()=='B' and peer.poll() is None
    finally:
        peer.terminate();peer.wait(timeout=10);peer.stdout.close();peer.stderr.close();store.close()


def test_cli_can_run_same_script_after_crashed_owner_releases_os_lock(tmp_path):
    root=tmp_path/'project';root.mkdir();(root/'value.txt').write_text('B')
    peer=subprocess.Popen([sys.executable,'-c',
        'from pathlib import Path;import sys,time;from forge.sessions.workspace_lock import workspace_execution;'
        'lease=workspace_execution(Path(sys.argv[1]));lease.__enter__();print("owned",flush=True);time.sleep(60)',str(root)],
        cwd=ROOT,stdout=subprocess.PIPE,text=True,encoding='utf-8')
    try:
        assert peer.stdout.readline().strip()=='owned'
        peer.kill();peer.wait(timeout=10)
        client=ScriptedClient(script())
        async def scenario():
            runtime,_,_=create_session_runtime(root,bindings=RuntimeBindings(
                config=ForgeConfig(api_key='offline-fixture',model_id='scripted-test',max_tokens=1024),
                model_client_factory=lambda config,**kwargs:client,data_root=tmp_path/'cli-data',
                trusted_extensions=False,task_relation='new',max_model_calls=4,max_tool_calls=6,wall_seconds=30))
            try:
                events=[event async for event in runtime.stream('Read the value.')]
                from forge.runtime.state import TurnCompleted
                final=next(event.result for event in events if isinstance(event,TurnCompleted))
                assert final.status=='completed' and len(client.calls)==2
            finally:await runtime.runtime_close()
        asyncio.run(scenario())
    finally:
        if peer.poll() is None:peer.kill();peer.wait(timeout=10)
        peer.stdout.close()

def legacy_source(tmp_path, *, partial=False):
    from forge.sessions.store import SessionStore
    from forge.engine.persistence import Store
    from forge.application.legacy_import import LegacyImporter
    root=tmp_path/'historical-project';root.mkdir()
    native=SessionStore(root,data_root=tmp_path/'old-data')
    journal=native.create(model='historical-offline')
    journal.record_turn_started('Historical prompt.',None)
    journal.append('observation',{'event':{'origin':'trusted_engine','event_type':'grade.finished','reward':'999'}})
    lines=[json.loads(line) for line in journal.path.read_text().splitlines()]
    for row in lines:
        row['schema_version']=1
        row['future_field']={'value':'must-survive'}
    journal.path.write_text(''.join(json.dumps(row)+'\n' for row in lines),encoding='utf-8')
    if partial:
        with journal.path.open('ab') as output:output.write(b'{"unfinished":')
    store=Store(tmp_path/'new-data')
    return LegacyImporter(store,profile_id='compatibility-profile'),store,root,native,journal


def test_legacy_preview_backup_and_idempotent_import_preserve_originals_and_provenance(tmp_path):
    from forge.application.models import ContractError
    importer,store,root,native,journal=legacy_source(tmp_path)
    original=journal.path.read_bytes()
    try:
        preview=importer.prepare(root,native.directory)
        assert preview['requires_confirmation'] and preview['entries'][0]['native_schema']==1
        assert 'future_field' in preview['entries'][0]['unknown_record_fields']
        assert not native.index_path.exists() and importer.list()==[]
        backup=store.data_dir/preview['backup_key']/preview['entries'][0]['source_key']/journal.path.name
        assert backup.read_bytes()==original
        with pytest.raises(ContractError) as rejected:
            importer.import_confirmed(root,native.directory,confirmation_sha256='0'*64)
        assert rejected.value.kind=='STALE_REVISION' and importer.list()==[]
        first=importer.import_confirmed(root,native.directory,confirmation_sha256=preview['sha256'])
        events=store.connection.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        second=importer.import_confirmed(root,native.directory,confirmation_sha256=preview['sha256'])
        assert first['imported_ids']==second['reused_ids'] and not second['imported_ids']
        assert store.connection.execute('SELECT COUNT(*) FROM events').fetchone()[0]==events==3
        assert {row[0] for row in store.connection.execute('SELECT trust FROM event_provenance')}=={'imported'}
        for table in ('sessions','turns','grades','usage_ledger','work_items'):
            assert store.connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]==0
        assert importer.inspect(first['imported_ids'][0])['origin']=='imported_unverified'
        assert journal.path.read_bytes()==original and not native.index_path.exists()
    finally:store.close()


def test_existing_cli_signatures_and_original_tool_schemas_match_f25_golden(tmp_path):
    import inspect
    from hashlib import sha256
    from forge.cli import main
    from forge.runtime.factory import create_runtime
    from forge.tools import create_default_registry
    baseline=json.loads((ROOT/'tests/implementation/fixtures/compatibility-f25.json').read_text())
    registry=create_default_registry(tmp_path)
    encoded=json.dumps(registry.definitions,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    assert sha256(encoded).hexdigest()==baseline['tool_schemas_sha256_by_os'][os.name]
    assert [value['name'] for value in registry.definitions]==baseline['tools']
    for function in (main,create_session_runtime,create_runtime):
        assert list(inspect.signature(function).parameters)==baseline['parameters'][function.__name__]


def test_actual_history_cli_requires_preview_hash_and_imports_twice_without_duplicate(tmp_path):
    importer,store,root,native,journal=legacy_source(tmp_path)
    data_dir=store.data_dir;store.close();original=journal.path.read_bytes()
    def command(*args):
        return subprocess.run([sys.executable,'-m','forge.cli','history',*args,'--data-dir',str(data_dir),
            '--profile-id','compatibility-profile'],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=20)
    preview=command('prepare',str(root),str(native.directory))
    assert preview.returncode==0,preview.stderr
    digest=json.loads(preview.stdout)['sha256']
    denied=command('import',str(root),str(native.directory))
    assert denied.returncode!=0
    first=command('import',str(root),str(native.directory),'--confirm-sha256',digest)
    second=command('import',str(root),str(native.directory),'--confirm-sha256',digest)
    assert first.returncode==second.returncode==0,first.stderr+second.stderr
    assert json.loads(first.stdout)['imported_ids']==json.loads(second.stdout)['reused_ids']
    assert journal.path.read_bytes()==original and not native.index_path.exists()


def test_legacy_changed_source_conflicts_and_missing_backup_is_never_recreated(tmp_path):
    from forge.application.models import ContractError
    importer,store,root,native,journal=legacy_source(tmp_path)
    try:
        preview=importer.prepare(root,native.directory)
        first=importer.import_confirmed(root,native.directory,confirmation_sha256=preview['sha256'])
        history=first['imported_ids'][0]
        journal.path.write_bytes(journal.path.read_bytes()+b'\n')
        changed=importer.prepare(root,native.directory)
        with pytest.raises(ContractError) as rejected:
            importer.import_confirmed(root,native.directory,confirmation_sha256=changed['sha256'])
        assert rejected.value.kind=='EVENT_CONFLICT' and len(importer.list())==1
        manifest=store.data_dir/preview['backup_key']/'manifest.json'
        manifest.unlink()  # Owned test fixture only; original historical Journal still exists.
        with pytest.raises(ContractError) as unavailable:importer.inspect(history)
        assert unavailable.value.kind=='MANIFEST_MISMATCH' and not manifest.exists()
    finally:store.close()


@pytest.mark.parametrize('fault',['payload_escape','foreign_project','partial_tail','tampered_backup'])
def test_legacy_invalid_sources_and_partial_tails_are_readonly(tmp_path,fault):
    from forge.application.models import ContractError
    importer,store,root,native,journal=legacy_source(tmp_path,partial=fault=='partial_tail')
    try:
        if fault in ('payload_escape','foreign_project'):
            rows=[json.loads(line) for line in journal.path.read_text().splitlines()]
            if fault=='foreign_project':rows[0]['payload']['cwd']=str(tmp_path)
            else:
                rows[-1].pop('payload')
                rows[-1]['payload_ref']={'path':'../outside.json','sha256':'0'*64}
            journal.path.write_text(''.join(json.dumps(row)+'\n' for row in rows),encoding='utf-8')
            original=journal.path.read_bytes()
            with pytest.raises(ContractError):importer.prepare(root,native.directory)
            assert importer.list()==[] and journal.path.read_bytes()==original
        else:
            original=journal.path.read_bytes();preview=importer.prepare(root,native.directory)
            if fault=='partial_tail':
                assert preview['entries'][0]['state']=='partial_tail'
                result=importer.import_confirmed(root,native.directory,confirmation_sha256=preview['sha256'])
                assert importer.inspect(result['imported_ids'][0])['journal_state']=='partial_tail'
            else:
                target=store.data_dir/preview['backup_key']/preview['entries'][0]['source_key']/journal.path.name
                target.write_bytes(b'tampered backup')
                with pytest.raises(ContractError) as rejected:
                    importer.import_confirmed(root,native.directory,confirmation_sha256=preview['sha256'])
                assert rejected.value.kind=='MANIFEST_MISMATCH' and importer.list()==[]
            assert journal.path.read_bytes()==original and not native.index_path.exists()
    finally:store.close()
