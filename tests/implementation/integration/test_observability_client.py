"""F22 uses actual Harness tools, Journal files, SQLite and artifact bytes."""
import asyncio
import base64
import json
from pathlib import Path
from forge.storage_paths import private_storage_path
import shutil
import subprocess
import sys

import pytest

from forge.application.models import ContractError, validate
from forge.engine.methods import EngineMethods
from forge.observability.export_queue import ObservationOptions
from forge.permissions.policy import ApprovalResponse
from forge.runtime.state import ModelTextDelta, ModelToolCallCompleted, ModelUsageUpdate, TokenUsage, ToolCall
from tests.implementation.integration.test_application import setup, ScriptedClient
from tests.implementation.unit.test_observability import book


def observed(tmp_path, capture='controlled_debug',stale_fixture=False):
    command='"'+sys.executable+'" -c "import sys;print(42);print(\'Bearer sensitive-do-not-persist\',file=sys.stderr)"'
    if stale_fixture: command='"'+sys.executable+'" -B -m unittest -v'
    responses=[[ModelUsageUpdate(TokenUsage(2,1)),ModelToolCallCompleted(ToolCall(0,'verify-real','verify',{'command':command}))],
        [ModelUsageUpdate(TokenUsage(2,1)),ModelTextDelta('Actual verification completed.')]]
    async def approve(request):
        return ApprovalResponse('allow_once')
    service,store,_,_,_,params=setup(tmp_path,factory=lambda config,**kw:ScriptedClient(responses),approval=approve,
        observation_options=ObservationOptions(capture_mode=capture,prices=book()))
    if stale_fixture:
        source=Path(__file__).parents[1]/'fixtures/stale-evidence/project'
        for path in source.iterdir(): shutil.copyfile(path,tmp_path/'project'/path.name)
    turn=service.start_turn(params)
    asyncio.run(service.execute_turn(turn['turn_id']))
    methods=EngineMethods(service,profile='test')
    execution=store.connection.execute('SELECT execution_id FROM execution_spans WHERE turn_id=?',(turn['turn_id'],)).fetchone()[0]
    return methods,turn['turn_id'],execution,params['session_id']


def test_actual_tool_message_correlates_execution_and_lazy_safe_output(tmp_path):
    methods,turn,execution,session=observed(tmp_path)
    try:
        snapshot=methods.handlers['session.snapshot']({'session_id':session})
        messages=[m for m in snapshot['messages'] if m['kind']=='tool' and m['status']=='completed']
        assert messages and messages[0]['execution_id']==execution
        assert methods.store.connection.execute('SELECT COUNT(*) FROM tool_output_views').fetchone()[0]==0
        query={'turn_id':turn,'execution_id':execution}
        result=methods.observations.output(query)
        validate('observability.output.result',result)
        assert result['state']=='available' and result['artifact_id']
        chunk=methods.artifacts.read_chunk({'artifact_id':result['artifact_id'],'offset':0,'length':262144})
        value=json.loads(base64.b64decode(chunk['data_base64']))
        assert set(value)=={'stdout','stderr'} and value['stdout'].strip()=='42'
        assert 'sensitive-do-not-persist' not in json.dumps(value) and 'import sys' not in json.dumps(value)
        assert 'metadata' not in value and 'content' not in value
        again=EngineMethods(methods.service,profile='test').observations.output(query)
        assert again==result and methods.store.connection.execute('SELECT COUNT(*) FROM tool_output_views').fetchone()[0]==1
        assert not any(key in result for key in ('path','local_name','script','thinking'))
    finally: methods.store.close()


def test_metadata_capture_never_returns_fake_tool_output(tmp_path):
    methods,turn,execution,_=observed(tmp_path,'metadata')
    try:
        result=methods.observations.output({'turn_id':turn,'execution_id':execution})
        assert result['state']=='unavailable' and result['artifact_id'] is None and result['reason']=='capture_disabled'
        assert methods.store.connection.execute('SELECT COUNT(*) FROM tool_output_views').fetchone()[0]==0
    finally: methods.store.close()


@pytest.mark.parametrize('change',['missing','changed'])
def test_actual_missing_or_changed_debug_capture_is_explicit_even_after_cached_view(tmp_path,change):
    methods,turn,execution,_=observed(tmp_path)
    try:
        methods.observations.output({'turn_id':turn,'execution_id':execution})
        path=next(private_storage_path(methods.store.data_dir/'harness').rglob('tool-'+execution+'.json'))
        if change=='missing': path.unlink()
        else: path.write_text('{"stdout":"changed actual bytes"}')
        result=methods.observations.output({'turn_id':turn,'execution_id':execution})
        assert result['state']=='missing' and result['artifact_id'] is None
    finally: methods.store.close()


def test_evidence_exposes_actual_content_revision_difference_without_invented_environment(tmp_path):
    methods,turn,_,_=observed(tmp_path,stale_fixture=True)
    async def run():
        query={'scope':{'kind':'turn','id':turn}}
        original=await methods.observations.evidence(query)
        subprocess.run([sys.executable,'invalidate.py'],cwd=tmp_path/'project',capture_output=True,check=True,timeout=10)
        changed=await methods.observations.evidence(query)
        a=original['items'][0]['metadata']['workspace_comparison']
        b=changed['items'][0]['metadata']['workspace_comparison']
        assert changed['items'][0]['validity']=='stale' and a['observed']==b['observed']
        assert b['current']['content_revision']>b['observed']['content_revision']
        assert b['current']['sha256']!=b['observed']['sha256'] and b['environment_state']=='unverified_current'
        assert 'return left - right' in (tmp_path/'project/calculator.py').read_text()
    try: asyncio.run(run())
    finally: methods.store.close()


def test_live_journal_projection_is_visible_before_model_finishes_and_does_not_duplicate_usage(tmp_path):
    gate=asyncio.Event();entered=asyncio.Event()
    class Streaming(ScriptedClient):
        async def stream(self,messages,tools=None,system=None):
            yield ModelTextDelta('First actual boundary text.')
            entered.set();await gate.wait()
            yield ModelUsageUpdate(TokenUsage(2,1))
    service,store,_,_,_,params=setup(tmp_path,factory=lambda config,**kw:Streaming([]),observation_options=ObservationOptions(prices=book()))
    async def run():
        methods=EngineMethods(service,profile='test');turn=service.start_turn(params);task=asyncio.create_task(service.execute_turn(turn['turn_id']))
        await asyncio.wait_for(entered.wait(),10)
        query={'scope':{'kind':'turn','id':turn['turn_id']}}
        try:
            async with asyncio.timeout(2):
                while not any(item['name']=='model.request.started' for item in methods.observations.spans(query)['items']):await asyncio.sleep(.02)
            assert store.connection.execute('SELECT state FROM turns WHERE id=?',(turn['turn_id'],)).fetchone()[0]=='running'
            assert any(item['name']=='model.request.started' for item in methods.observations.spans(query)['items'])
            assert methods.observations.usage(query)['request_count']==1
            assert any(item['metadata']['first_client_text_chunk_monotonic_ns'] for item in methods.observations.spans(query)['items'])
        finally: gate.set();await task
        assert methods.observations.usage(query)['request_count']==1
        assert methods.observations.timings({'turn_id':turn['turn_id']})['harness_wall_seconds']
    try: asyncio.run(run())
    finally: store.close()


def test_filtered_completion_event_cursor_cannot_cross_filters_or_profiles(tmp_path):
    methods,turn,_,_=observed(tmp_path)
    try:
        query={'scope':{'kind':'turn','id':turn},'event_types':['completion.accepted','completion.rejected'],'limit':1}
        page=methods.query_events(query)
        assert page['items'] and all(e['event_type'].startswith('completion.') for e in page['items'])
        cursor=methods.events.cursor({'scope':query['scope'],'event_types':query['event_types']},int(page['items'][0]['store_seq']))
        with pytest.raises(ContractError) as error: methods.query_events({**query,'cursor':cursor,'event_types':['sandbox.prepared']})
        assert error.value.kind=='INVALID_CURSOR'
        methods.service.profile_id='another-profile'
        assert methods.query_events({'scope':{'kind':'all'}})['items']==[]
    finally: methods.store.close()


def test_actual_completion_rejection_exposes_obligations_and_execution_link(tmp_path):
    responses=[[ModelUsageUpdate(TokenUsage(2,1)),ModelToolCallCompleted(ToolCall(0,'actual-write','apply_patch',{'patch':'*** Begin Patch\n*** Update File: value.txt\n@@\n-B\n+C\n*** End Patch'}))],
        [ModelUsageUpdate(TokenUsage(2,1)),ModelToolCallCompleted(ToolCall(0,'unverified-finish','finish_task',{'task_kind':'change','status':'blocked','summary':'Verification was not performed.','blocked_reasons':['Verification not performed.']}))]]
    async def approve(request):return ApprovalResponse('allow_once')
    service,store,_,_,_,params=setup(tmp_path,factory=lambda config,**kw:ScriptedClient(responses),approval=approve)
    try:
        turn=service.start_turn(params);result=asyncio.run(service.execute_turn(turn['turn_id']))
        methods=EngineMethods(service,profile='test')
        page=methods.query_events({'scope':{'kind':'turn','id':turn['turn_id']},'event_types':['completion.rejected']})
        assert page['items'] and result.status!='completed'
        assert page['items'][0]['attributes']['reason']
        assert page['items'][0]['attributes']['completion_report']['unmet_requirements']
        assert page['items'][0]['turn_id']==turn['turn_id'] and page['items'][0]['execution_id'] is None
        span=methods.observations.spans({'scope':{'kind':'turn','id':turn['turn_id']}})
        assert any(s['span_id']==page['items'][0]['span_id'] for s in span['items'])
        assert (tmp_path/'project/value.txt').read_text().strip()=='C'
    finally: store.close()


def test_read_file_content_has_no_tool_stream_reader_even_with_debug_enabled(tmp_path):
    service,store,_,_,_,params=setup(tmp_path,observation_options=ObservationOptions(capture_mode='controlled_debug'))
    (tmp_path/'project/value.txt').write_text('PRIVATE_RAW_SCRIPT: import os; HIDDEN_REASONING')
    try:
        turn=service.start_turn(params);asyncio.run(service.execute_turn(turn['turn_id']));methods=EngineMethods(service,profile='test')
        execution=store.connection.execute('SELECT execution_id FROM execution_spans WHERE turn_id=? ORDER BY rowid LIMIT 1',(turn['turn_id'],)).fetchone()[0]
        result=methods.observations.output({'turn_id':turn['turn_id'],'execution_id':execution})
        assert result['state']=='unavailable' and result['artifact_id'] is None and result['reason']=='no_safe_streams'
        assert 'PRIVATE_RAW_SCRIPT' not in json.dumps(result)
    finally: store.close()


def test_profile_scope_and_execution_binding_deny_foreign_observations(tmp_path):
    methods,turn,execution,_=observed(tmp_path)
    try:
        methods.service.profile_id='another-profile'
        foreign=EngineMethods(methods.service,profile='test').observations
        for operation,params in [(foreign.spans,{'scope':{'kind':'turn','id':turn}}),
            (foreign.context,{'turn_id':turn}),(foreign.output,{'turn_id':turn,'execution_id':execution})]:
            with pytest.raises(ContractError) as error: operation(params)
            assert error.value.kind in ('UNAUTHORIZED','NOT_FOUND')
        assert foreign.usage({'scope':{'kind':'all'}})['request_count']==0
        assert foreign.spans({'scope':{'kind':'all'}})['items']==[]
    finally: methods.store.close()


def test_repeated_pages_usage_and_monotonic_timings_are_actual_and_not_parallel_sums(tmp_path):
    methods,turn,execution,_=observed(tmp_path)
    try:
        query={'scope':{'kind':'turn','id':turn}}
        first=methods.observations.usage(query)
        assert first==methods.observations.usage(query) and first['request_count']==2
        page=methods.observations.spans({**query,'limit':2})
        items=list(page['items'])
        while page['next_cursor']:
            page=methods.observations.spans({**query,'limit':2,'cursor':page['next_cursor']});items.extend(page['items'])
        assert len(items)==len({(s['trace_id'],s['span_id']) for s in items})
        assert all('start_monotonic_ns' in s['metadata'] for s in items)
        exact=methods.observations.spans({**query,'execution_id':execution})
        assert len(exact['items'])==1 and exact['items'][0]['metadata']['execution_id']==execution
        timing=methods.observations.timings({'turn_id':turn})
        validate('observability.timings.result',timing)
        assert int(timing['engine_duration_nanoseconds'])>0 and float(timing['harness_wall_seconds'])>0
        assert timing['clock_source']=='local_monotonic' and timing['server_first_token_time'] is None
    finally: methods.store.close()
