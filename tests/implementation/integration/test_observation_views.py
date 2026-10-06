"""F18 runs real Harness, SQLite, provider SDK HTTP and collector HTTP boundaries."""
import asyncio
from decimal import Decimal
import json
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

import pytest

from forge.application.models import validate,ContractError
from forge.engine.methods import EngineMethods
from forge.engine.persistence import new_id
from forge.engine.journal_projection import JournalProjector
from forge.observability.export_queue import ObservationOptions
from forge.observability.usage_ledger import PriceBook,confirm_usage
from forge.runtime.state import ModelTextDelta,ModelUsageUpdate,ModelToolCallCompleted,TokenUsage,ToolCall
from tests.implementation.integration.test_application import setup,ScriptedClient
from tests.implementation.integration.test_traces import all_events
from tests.implementation.unit.test_observability import book


def views(service):
    return EngineMethods(service,profile='test').observations


def test_real_request_ledger_frozen_prices_and_replay_recompute_once(tmp_path):
    service,store,_,_,_,params=setup(tmp_path,observation_options=ObservationOptions(prices=book()))
    try:
        turn=service.start_turn(params)
        store.observation_options=ObservationOptions()  # Later settings do not reprice this accepted turn.
        result=asyncio.run(service.execute_turn(turn['turn_id']))
        assert result.status=='completed'
        query=views(service)
        usage=query.usage({'scope':{'kind':'turn','id':turn['turn_id']}})
        validate('observability.usage.result',usage)
        assert usage['request_count']==2 and usage['input_tokens']==23 and usage['output_tokens']==7
        assert usage['cost_decimal']==usage['known_cost_decimal']=='0.000174' and usage['unknown_requests']==0
        assert Decimal(usage['cost_decimal'])==sum(Decimal(r[0]) for r in store.connection.execute('SELECT cost FROM usage_ledger'))
        assert usage['roles']['main']['requests']==2 and usage['pricing_snapshot']
        native=store.connection.execute('SELECT native_ref FROM turns WHERE id=?',(turn['turn_id'],)).fetchone()[0]
        path=next((store.data_dir/'harness').rglob(native+'.jsonl'))
        assert JournalProjector(store).project(path,params['session_id'],trusted=True)==0
        assert query.usage({'scope':{'kind':'all'}})['cost_decimal']=='0.000174'
        spans=query.spans({'scope':{'kind':'turn','id':turn['turn_id']},'limit':1})
        validate('observability.spans.result',spans)
        assert spans['next_cursor']
        with pytest.raises(ContractError):
            query.context({'turn_id':turn['turn_id'],'cursor':spans['next_cursor']})
        context=query.context({'turn_id':turn['turn_id']})
        validate('observability.context.result',context)
        assert len(context['items'])==2 and context['items'][1]['version']>context['items'][0]['version']
    finally:
        store.close()


def test_unknown_then_actual_usage_updates_same_row_and_conflicting_confirmation_is_rejected(tmp_path):
    service,store,_,_,_,params=setup(tmp_path,factory=lambda config,**kw:ScriptedClient([[ModelTextDelta('Observed.')]]),
        observation_options=ObservationOptions(prices=book()))
    try:
        turn=service.start_turn(params)
        asyncio.run(service.execute_turn(turn['turn_id']))
        query=views(service)
        initial=query.usage({'scope':{'kind':'all'}})
        assert initial['cost_decimal'] is None and initial['input_tokens'] is None and initial['unknown_requests']==1
        request=store.connection.execute('SELECT id FROM model_requests').fetchone()[0]
        raw={'input_tokens':5,'output_tokens':1}
        confirm_usage(store,request,raw,provider_request_id='real-confirmation-fixture')
        confirmed=query.usage({'scope':{'kind':'all'}})
        assert confirmed['request_count']==1 and confirmed['cost_decimal']=='0.00003' and confirmed['unknown_requests']==0
        confirm_usage(store,request,raw,provider_request_id='real-confirmation-fixture')
        assert store.connection.execute('SELECT COUNT(*) FROM usage_ledger').fetchone()[0]==1
        with pytest.raises(ContractError) as error:
            confirm_usage(store,request,{'input_tokens':99,'output_tokens':1})
        assert error.value.kind=='EVENT_CONFLICT'
        with pytest.raises(ContractError) as error:
            confirm_usage(store,request,{'input_tokens':True,'output_tokens':1})
        assert error.value.kind=='EVENT_CONFLICT'
        assert query.usage({'scope':{'kind':'all'}})['cost_decimal']=='0.00003'
    finally:
        store.close()


def test_explore_parent_summary_is_not_a_second_cost_row(tmp_path):
    report=json.dumps({'summary':'Observed B.','relevant_files':[{'path':'value.txt','relevance':'Actual read'}]})
    main=[[ModelUsageUpdate(TokenUsage(2,1)),ModelToolCallCompleted(ToolCall(0,'explore','explore_repository',{'question':'Read value.txt.'}))],
        [ModelUsageUpdate(TokenUsage(2,1)),ModelToolCallCompleted(ToolCall(0,'finish','finish_task',{'task_kind':'answer','status':'completed','summary':'Observed.'}))]]
    child=[[ModelTextDelta(report),ModelUsageUpdate(TokenUsage(3,1))]]
    clients=[]
    def factory(config,**kw):
        client=ScriptedClient(child if len(clients)>=2 else main)
        clients.append(client)
        return client
    service,store,_,_,_,params=setup(tmp_path,factory=factory,observation_options=ObservationOptions(prices=book()))
    try:
        turn=service.start_turn(params)
        result=asyncio.run(service.execute_turn(turn['turn_id']))
        assert result.status=='completed'
        usage=views(service).usage({'scope':{'kind':'all'}})
        assert usage['request_count']==3 and usage['cost_decimal']=='0.000066'
        assert usage['roles']['explore']['requests']==1 and usage['roles']['main']['requests']==2
        assert len({e['trace_id'] for e in all_events(store) if e['event_type']=='model.request.started'})==1
    finally:
        store.close()


def test_verification_query_observes_actual_external_edit_and_environment_invalidation(tmp_path):
    import sys
    from forge.permissions.policy import ApprovalResponse
    command='"'+sys.executable+'" -c "print(42)"'
    responses=[[ModelUsageUpdate(TokenUsage(2,1)),ModelToolCallCompleted(ToolCall(0,'verify','verify',{'command':command}))],
        [ModelUsageUpdate(TokenUsage(2,1)),ModelTextDelta('Observed.')]]
    async def approve(request):
        return ApprovalResponse('allow_once')
    service,store,_,_,_,params=setup(tmp_path,factory=lambda config,**kw:ScriptedClient(responses),approval=approve)
    async def run():
        turn=service.start_turn(params)
        await service.execute_turn(turn['turn_id'])
        query=views(service)
        original=await query.evidence({'scope':{'kind':'turn','id':turn['turn_id']}})
        validate('observability.evidence.result',original)
        assert original['items'][0]['validity']=='current' and original['items'][0]['metadata']['evidence_metadata']['exit_code']==0
        (tmp_path/'project'/'value.txt').write_text('Changed externally.')
        changed=await query.evidence({'scope':{'kind':'turn','id':turn['turn_id']}})
        assert changed['items'][0]['validity']=='stale' and changed['items'][0]['reason']=='workspace_content_changed_since_verification'
        assert not any(e['event_type']=='grade.finished' for e in all_events(store))
    try:
        asyncio.run(run())
    finally:
        store.close()


def test_real_collector_failure_bounded_queue_and_success_do_not_change_agent_state(tmp_path):
    received=[]
    class Collector(BaseHTTPRequestHandler):
        successful=False
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200 if self.successful else 503)
            self.send_header('Content-Type','application/json')
            self.end_headers()
            self.wfile.write(b'{}')
        def log_message(self,*args):
            pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Collector)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    options=ObservationOptions(endpoint=f'http://127.0.0.1:{server.server_port}/v1/traces',metadata_export_confirmed=True,queue_items=3)
    service,store,_,_,_,params=setup(tmp_path,observation_options=options)
    async def run():
        try:
            turn=service.start_turn(params)
            result=await service.execute_turn(turn['turn_id'])
            assert result.status=='completed'
            for _ in range(30):
                if any(f['reason']=='http_503' for f in service.exporter.status()['failures']): break
                await asyncio.sleep(.02)
            status=service.exporter.status()
            assert received and status['pending_items']<=3
            assert {f['reason'] for f in status['failures']} >= {'queue_capacity','http_503'}
            assert 'sensitive-do-not-persist' not in json.dumps(received) and 'value.txt' not in json.dumps(received)
            span=received[0]['resourceSpans'][0]['scopeSpans'][0]['spans'][0]
            assert isinstance(span['startTimeUnixNano'],str) and len(span['traceId'])==32 and type(span['kind']) is int
            Collector.successful=True
            for _ in range(200):
                if service.exporter.status()['pending_items']==0: break
                await asyncio.sleep(.02)
            assert service.exporter.status()['pending_items']==0
            assert store.connection.execute('SELECT outcome FROM turns WHERE id=?',(turn['turn_id'],)).fetchone()[0]=='completed'
        finally:
            await service.exporter.aclose()
    try:
        asyncio.run(run())
    finally:
        server.shutdown()
        server.server_close()
        store.close()


@pytest.mark.parametrize('interrupted',[False,True])
def test_real_provider_sdk_http_retry_cache_usage_and_interrupted_stream(tmp_path,interrupted):
    from forge.config import ForgeConfig
    from forge.runtime.providers import NativeModelClient
    calls=[]
    class Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            if len(calls)==1 and not interrupted:
                body=b'{"error":{"message":"Controlled retry fixture","type":"server_error"}}'
                self.send_response(503)
                self.send_header('Content-Type','application/json')
            else:
                delta={'type':'response.output_text.delta','delta':'Observed.','item_id':'message-1','output_index':0,'content_index':0}
                events=[delta]
                if not interrupted:
                    final={'id':'resp-actual-local-fixture','object':'response','created_at':1,'status':'completed','model':'scripted-test',
                        'output':[{'id':'message-1','type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':'Observed.','annotations':[]}]}],
                        'usage':{'input_tokens':100,'output_tokens':10,'total_tokens':110,
                            'input_tokens_details':{'cached_tokens':30,'cache_write_tokens':20},'output_tokens_details':{'reasoning_tokens':0}}}
                    events.append({'type':'response.completed','response':final})
                body=(''.join('data: '+json.dumps(e)+'\n\n' for e in events)+'data: [DONE]\n\n').encode()
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self,*args):
            pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    source=book().as_dict()
    source['rates'][0]['provider']='openai_responses'
    service,store,vault,_,_,params=setup(tmp_path,factory=lambda config,**kw:NativeModelClient(config,max_attempts=2),
        observation_options=ObservationOptions(prices=PriceBook(source)))
    config=ForgeConfig(api_key='synthetic-local-provider-key',provider='openai_responses',model_id='scripted-test',
        base_url=f'http://127.0.0.1:{server.server_port}/v1')
    connection=service.put_connection(config)
    vault.keys[connection]=config.api_key
    try:
        turn=service.start_turn({**params,'connection_id':connection})
        result=asyncio.run(service.execute_turn(turn['turn_id']))
        assert result.status==('failed' if interrupted else 'completed')
        usage=views(service).usage({'scope':{'kind':'all'}})
        starts=[e for e in all_events(store) if e['event_type']=='model.request.started']
        assert len(starts)==len(calls)==usage['request_count']==(1 if interrupted else 2)
        assert usage['unknown_requests']==1 and usage['cost_decimal'] is None
        assert len({e['attributes']['invocation_id'] for e in starts})==1
        if not interrupted:
            assert usage['known_cost_decimal']=='0.000384'
            assert int(starts[1]['attributes']['observed_backoff_nanoseconds'])>0
            row=store.connection.execute("SELECT u.raw_usage,u.normalized_usage,m.provider_request_id FROM usage_ledger u JOIN model_requests m ON m.id=u.request_id WHERE u.quality='actual'").fetchone()
            assert json.loads(row[0])['input_tokens_details']['cache_write_tokens']==20
            assert json.loads(row[1])['input_tokens']==100 and row[2]=='resp-actual-local-fixture'
        spans=views(service).spans({'scope':{'kind':'turn','id':turn['turn_id']}})['items']
        assert any(s['metadata']['first_client_text_chunk_at_utc'] for s in spans)
    finally:
        server.shutdown()
        server.server_close()
        store.close()


@pytest.mark.parametrize('interrupted',[False,True])
def test_real_anthropic_sdk_partial_usage_does_not_become_a_complete_bill(tmp_path,interrupted):
    from forge.config import ForgeConfig
    from forge.runtime.model_client import AnthropicModelClient
    calls=[]
    class Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            events=[{'type':'message_start','message':{'id':'message-http-fixture','type':'message','role':'assistant',
                'model':'scripted-test','content':[],'stop_reason':None,'stop_sequence':None,'usage':{'input_tokens':12,'output_tokens':0}}},
                {'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}},
                {'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'Observed.'}},
                {'type':'content_block_stop','index':0}]
            if not interrupted:
                events.extend([{'type':'message_delta','delta':{'stop_reason':'end_turn','stop_sequence':None},'usage':{'output_tokens':3}},
                    {'type':'message_stop'}])
            body=''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode()
            self.send_response(200)
            self.send_header('Content-Type','text/event-stream')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self,*args):
            pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    service,store,vault,_,_,params=setup(tmp_path,
        factory=lambda config,**kw:AnthropicModelClient.from_config(config,max_retries=0),
        observation_options=ObservationOptions(prices=book()))
    config=ForgeConfig(api_key='synthetic-loopback-key',provider='anthropic',model_id='scripted-test',
        base_url=f'http://127.0.0.1:{server.server_port}')
    connection=service.put_connection(config)
    vault.keys[connection]=config.api_key
    try:
        turn=service.start_turn({**params,'connection_id':connection})
        result=asyncio.run(service.execute_turn(turn['turn_id']))
        usage=views(service).usage({'scope':{'kind':'turn','id':turn['turn_id']}})
        assert len(calls)==usage['request_count']==1
        assert result.status==('failed' if interrupted else 'completed')
        row=store.connection.execute('SELECT raw_usage,quality FROM usage_ledger').fetchone()
        assert json.loads(row[0])['input_tokens']==12
        assert usage['cost_decimal']==(None if interrupted else '0.000081')
        assert usage['unknown_requests']==int(interrupted) and row[1]==('unknown' if interrupted else 'actual')
    finally:
        server.shutdown()
        server.server_close()
        store.close()


def test_optional_debug_disk_failure_does_not_block_agent(tmp_path,monkeypatch):
    from pathlib import Path
    original=Path.mkdir
    def fail(path,*args,**kwargs):
        if 'controlled-debug' in path.parts:
            raise OSError('Controlled debug disk failure')
        return original(path,*args,**kwargs)
    service,store,_,_,_,params=setup(tmp_path,observation_options=ObservationOptions(capture_mode='controlled_debug'))
    monkeypatch.setattr(Path,'mkdir',fail)
    try:
        turn=service.start_turn(params)
        result=asyncio.run(service.execute_turn(turn['turn_id']))
        assert result.status=='completed'
        context=views(service).context({'turn_id':turn['turn_id']})
        capture=context['items'][0]['metadata']['context_metadata']['debug_capture']
        assert capture=={'state':'omitted','reason':'OSError'}
    finally:
        store.close()


def test_actual_compaction_bills_summary_once_and_records_exact_tool_pair_retention(tmp_path):
    from forge.config import ForgeConfig
    from forge.runtime.dependencies import RuntimeBindings
    from forge.runtime.factory import create_runtime
    from forge.runtime.runner import TurnRunner
    from forge.observability.recorder import JournalRecorder
    from tests.context.test_summary import SummaryClient,valid_summary
    service,store,_,_,_,params=setup(tmp_path,observation_options=ObservationOptions(prices=book()))
    client=SummaryClient(valid_summary())
    client.provider='anthropic'
    client.model='scripted-test'
    turn=service.start_turn(params)
    work=store.connection.execute('SELECT * FROM work_items WHERE business_id=?',(turn['turn_id'],)).fetchone()
    store.claim_work_item(work['id'],expected_version=work['version'],emit_turn_event=True)
    conversation,journal,_=create_runtime(tmp_path/'project',bindings=RuntimeBindings(
        config=ForgeConfig(api_key='synthetic',model_id='scripted-test'),model_client_factory=lambda config,**kw:client,
        data_root=store.data_dir/'harness',trusted_extensions=False,task_relation='new'))
    journal.observation_options=service.observation_options
    recorder=JournalRecorder(journal,scope=store.trace_scope(turn['turn_id']))
    runner=TurnRunner(conversation)
    runner.messages=[{'role':'user','content':'Preserve this exact requirement.'},
        {'role':'assistant','content':[{'type':'tool_use','id':'actual-call','name':'read_file','input':{'path':'value.txt'}}]},
        {'role':'user','content':[{'type':'tool_result','tool_use_id':'actual-call','content':'B'*40000}]},
        {'role':'user','content':'Latest request.'}]
    async def run():
        try:
            with recorder.turn():
                runner.state.request_event_sink=conversation.record_model_request
                await runner._compact('Summarize.',None,force=True)
        finally:
            await conversation.runtime_close()
    try:
        asyncio.run(run())
        JournalProjector(store).project(journal.path,params['session_id'],trusted=True)
        query=views(service)
        usage=query.usage({'scope':{'kind':'turn','id':turn['turn_id']}})
        assert usage['request_count']==1 and usage['cost_decimal']=='0.0006'
        assert usage['roles']['summary']['requests']==1
        finished=next(e for e in all_events(store) if e['event_type']=='compaction.finished')
        metadata=finished['attributes']['context_metadata']
        assert finished['attributes']['summary_request_ids'] and metadata['before']['pairing_complete']
        assert metadata['after']['pairing_complete'] and metadata['retention_check_complete']
        assert metadata['check_kind']=='exact_original_message_presence'
        assert metadata['retained_constraint_message_ids']==metadata['before']['constraint_message_ids']
        assert metadata['after']['estimated_tokens']<metadata['before']['estimated_tokens']
        assert JournalProjector(store).project(journal.path,params['session_id'],trusted=True)==0
        assert query.usage({'scope':{'kind':'all'}})['cost_decimal']=='0.0006'
    finally:
        store.close()


def test_upgrade_legacy_observation_tables_backfills_attribution_without_guessing_prices(tmp_path):
    from forge.engine.persistence import Store
    service,store,_,_,_,params=setup(tmp_path)
    turn=service.start_turn(params)
    asyncio.run(service.execute_turn(turn['turn_id']))
    # Retain committed events, request ledger and spans in their actual F17 schema.
    with store.transaction():
        for table in ('request_details','span_details','context_snapshots','evidence_details','export_queue','export_failures','turn_observation_config'):
            if table=='context_snapshots':
                store.connection.execute('DELETE FROM context_snapshots')  # F17 did not project this view.
                for column in ('metadata_json','snapshot_ref'):
                    store.connection.execute('ALTER TABLE context_snapshots DROP COLUMN '+column)
            else:
                store.connection.execute('DROP TABLE '+table)
        for column in ('currency','cost_quality'):
            store.connection.execute('ALTER TABLE usage_ledger DROP COLUMN '+column)
        store.connection.execute('PRAGMA user_version=7')
        store.connection.execute('DELETE FROM schema_migrations WHERE version=8')
    count=store.connection.execute('SELECT COUNT(*) FROM events').fetchone()[0]
    store.close()
    with Store(tmp_path/'data') as reopened:
        assert not reopened.read_only and reopened.diagnostics()['schema_version']==8
        assert reopened.connection.execute('SELECT COUNT(*) FROM request_details').fetchone()[0]==2
        assert reopened.connection.execute('SELECT COUNT(*) FROM spans').fetchone()[0]==reopened.connection.execute('SELECT COUNT(*) FROM span_details').fetchone()[0]
        assert reopened.connection.execute('SELECT COUNT(*) FROM events').fetchone()[0]==count
        assert list((tmp_path/'data'/'backups').glob('*.sqlite3'))
        service.store=reopened
        service.exporter.store=reopened
        usage=views(service).usage({'scope':{'kind':'turn','id':turn['turn_id']}})
        assert usage['request_count']==2 and usage['input_tokens']==23
        assert usage['cost_decimal'] is None and usage['unknown_requests']==2


@pytest.mark.parametrize('mode',['metadata','controlled_debug'])
def test_debug_capture_is_opt_in_local_and_redacts_literal_credential(tmp_path,mode):
    service,store,_,_,_,params=setup(tmp_path,observation_options=ObservationOptions(capture_mode=mode))
    params={**params,'input':[{'type':'text','text':'sensitive-do-not-persist is synthetic fixture content.'}]}
    try:
        turn=service.start_turn(params)
        asyncio.run(service.execute_turn(turn['turn_id']))
        files=list((store.data_dir/'harness').rglob('controlled-debug/**/*.json'))
        assert bool(files)==(mode=='controlled_debug')
        for path in files:
            assert b'sensitive-do-not-persist' not in path.read_bytes()
        model_files=[path for path in files if path.name.startswith('model-')]
        assert bool(model_files)==(mode=='controlled_debug')
        if model_files:
            assert all(json.loads(path.read_text())['scope']=='client_text_deltas' for path in model_files)
        assert not service.exporter.status()['enabled']
    finally:
        store.close()
