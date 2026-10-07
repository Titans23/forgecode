"""Offline regression candidates: real Harness/files/TCP, scripted model only."""
import asyncio
from dataclasses import replace
import json
import sys
import pytest
from forge.runtime.agent_loop import Conversation
from forge.runtime.completion import TaskPolicy, checker_revision_covers
from forge.runtime.delivery import completion_report
from forge.runtime.model_budget import BudgetedModelClient, request_observer
from forge.runtime.state import ModelTextDelta, ModelToolCallCompleted, ModelUsageUpdate, TokenUsage, ToolCall, TurnCompleted, VerificationEvidence
from forge.runtime.turn_state import TurnState, parent_budget
from forge.tools import create_default_registry
from forge.permissions.policy import PermissionManager, ApprovalResponse
from forge.context.compactor import CompactionConfig


def test_pending_cancel_stops_actual_loopback_request_after_legacy_swallow():
    async def run():
        requests=[]
        async def accept(reader,writer):
            requests.append(await reader.readline())
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n')
            await writer.drain();writer.close();await writer.wait_closed()
        server=await asyncio.start_server(accept,'127.0.0.1',0)
        port=server.sockets[0].getsockname()[1]
        class LegacyClient:
            provider='scripted-loopback'
            observes_request_budget=True
            async def stream(self,**kwargs):
                asyncio.current_task().cancel()
                try:await asyncio.sleep(0)
                except asyncio.CancelledError:pass
                request_observer.get()()
                reader,writer=await asyncio.open_connection('127.0.0.1',port)
                writer.write(b'GET /offline HTTP/1.1\r\nHost: localhost\r\n\r\n')
                await writer.drain();await reader.read();writer.close();await writer.wait_closed()
                yield ModelUsageUpdate(TokenUsage(1,1))
        state=TurnState(max_model_calls=3)
        async def consume():
            async for _ in BudgetedModelClient(LegacyClient(),state).stream([]):pass
        try:
            with pytest.raises(asyncio.CancelledError):await asyncio.create_task(consume())
            assert requests==[] and state.model_calls==0
        finally:server.close();await server.wait_closed()
    asyncio.run(run())


@pytest.mark.parametrize('same_command',[False,True])
def test_stale_success_never_masks_current_failure_in_delivery(same_command):
    check={'key':'value','operator':'eq','expected':42,'requirement':'value 42','requirement_id':'req-1','expected_source':'original source'}
    old=VerificationEvidence('python current.py','.',1,.1,False,2,environment_epoch=2,
        verification_id='current-failed',output_checks=(check,),requirement_ids=('req-1',))
    stale=replace(old,command=old.command if same_command else 'python prior.py',exit_code=0,
        workspace_revision=1,environment_epoch=1,verification_id='stale-success',asserted_requirement_ids=('req-1',),
        supersedes=('current-failed',),revision_reason='Checker contract preserved')
    # Assertion inheritance remains legal; freshness is checked by delivery/gate.
    assert checker_revision_covers(old,stale)
    report=completion_report(status='partial',evidence=(old,stale),workspace_revision=2,environment_epoch=2,reasons=('failed',),has_contract=True)
    assert report.failed_checks==('current-failed',) and report.verification_status=='failed_checks'
    fresh=replace(stale,workspace_revision=2,environment_epoch=2,verification_id='fresh-success')
    fixed=completion_report(status='completed',evidence=(old,stale,fresh),workspace_revision=2,environment_epoch=2,reasons=(),has_contract=True)
    assert not fixed.failed_checks and fixed.verification_status=='passed_checks'


def test_two_child_harnesses_share_request_and_cumulative_usage_budget_once(tmp_path):
    async def run():
        events=[];root=TurnState(max_model_calls=2,request_event_sink=lambda kind,value:events.append((kind,value)))
        class Client:
            provider='scripted'
            calls=0
            async def stream(self,**kwargs):
                self.calls+=1
                yield ModelUsageUpdate(TokenUsage(2,1),usage_is_final=False)
                yield ModelUsageUpdate(TokenUsage(4,2),usage_is_final=True)
                yield ModelTextDelta('Read-only answer')
        clients=[Client() for _ in range(3)];results=[]
        token=parent_budget.set(root)
        try:
            for client in clients:
                conversation=Conversation(client=client,context_root=tmp_path,include_task_tools=False,max_iterations=8)
                async for event in conversation.stream('Answer without tools.'):
                    if isinstance(event,TurnCompleted):results.append(event.result)
        finally:parent_budget.reset(token)
        assert root.model_calls==2 and root.usage==TokenUsage(8,4)
        assert [c.calls for c in clients]==[1,1,0]
        assert results[-1].model_calls==0 and results[-1].stop_reason=='model_budget_exhausted'
        starts=[v['request_id'] for k,v in events if k=='model_request_started']
        assert len(starts)==len(set(starts))==2
        assert sum(k=='model_request_finished' for k,_ in events)==2
    asyncio.run(run())


def test_real_long_read_full_compaction_keeps_pairs_and_original_constraints(tmp_path):
    (tmp_path/'large.txt').write_text('context evidence\n'*4000,encoding='utf-8')
    constraint='只读取 large.txt，不得删除文件。'
    class Client:
        provider='scripted'
        main_calls=0;summary_calls=0
        async def stream(self,messages,tools=None,system=None):
            yield ModelUsageUpdate(TokenUsage(4,1),usage_is_final=True)
            if tools is None:
                self.summary_calls+=1
                yield ModelTextDelta(json.dumps({'goal':constraint,'constraints':[constraint],'findings':['Read actual file'],
                    'modified_files':[],'failed_attempts':[],'verification':[],'open_questions':[],'next_action':'Answer'}))
            else:
                self.main_calls+=1
                if self.main_calls==1:yield ModelToolCallCompleted(ToolCall(0,'read','read_file',{'path':'large.txt'}))
                else:
                    uses=[b['id'] for m in messages if isinstance(m.get('content'),list) for b in m['content'] if b.get('type')=='tool_use']
                    results=[b['tool_use_id'] for m in messages if isinstance(m.get('content'),list) for b in m['content'] if b.get('type')=='tool_result']
                    assert sorted(uses)==sorted(results)
                    assert constraint in str(messages)
                    yield ModelToolCallCompleted(ToolCall(0,'finish','finish_task',{'task_kind':'answer','status':'completed','summary':'Read actual file.'}))
    async def run():
        client=Client();conversation=Conversation(client=client,context_root=tmp_path,registry=create_default_registry(tmp_path),max_iterations=5,
            context_config=CompactionConfig(auto_compact_characters=1500,tool_result_inline_limit=1024,post_compact_file_budget=5000))
        async for event in conversation.stream(constraint):
            if isinstance(event,TurnCompleted):result=event.result
        assert result.status=='completed' and client.main_calls==2 and client.summary_calls==1
        assert result.model_calls==3
        assert list((tmp_path/'.forge/context').rglob('*.txt'))
        assert (tmp_path/'large.txt').read_text(encoding='utf-8')=='context evidence\n'*4000
    asyncio.run(run())


@pytest.mark.parametrize('repairs',[0,2])
def test_repair_zero_and_two_execute_different_paths_under_identical_limits(tmp_path,repairs):
    class Client:
        provider='scripted'
        calls=0
        async def stream(self,**kwargs):
            self.calls+=1;yield ModelUsageUpdate(TokenUsage(1,1),usage_is_final=True)
            if self.calls==2:yield ModelToolCallCompleted(ToolCall(0,'write','write_file',{'path':'delivery.txt','content':'verified'}))
            elif self.calls==4:yield ModelToolCallCompleted(ToolCall(0,'verify','verify',{
                'command':'"'+sys.executable+'" -','stdin':"from pathlib import Path\nassert Path('delivery.txt').read_text()=='verified'\nprint('checked')\n"}))
            else:yield ModelTextDelta('Delivery complete.')
    async def approve(request):return ApprovalResponse('allow_once','Controlled offline fixture execution')
    async def run():
        client=Client();conversation=Conversation(client=client,context_root=tmp_path,registry=create_default_registry(tmp_path),
            permission_manager=PermissionManager(tmp_path,mode='supervised',approval_handler=approve,load_stored_rules=False),
            max_iterations=5,max_tool_calls=2,max_turn_seconds=30,
            task_policy=TaskPolicy(require_verification=True,required_paths=('delivery.txt',),max_delivery_repairs=repairs))
        async for event in conversation.stream('Create delivery.txt and verify its exact content.'):
            if isinstance(event,TurnCompleted):result=event.result
        assert (conversation.max_iterations,conversation.max_tool_calls,conversation.max_turn_seconds)==(5,2,30)
        assert result.model_calls==client.calls==(1 if repairs==0 else 5)
        assert result.status==('partial' if repairs==0 else 'completed')
        if repairs:
            assert result.verification.success and result.verification.workspace_revision==conversation.turn_state.workspace_revision
            assert (tmp_path/'delivery.txt').read_text()=='verified'
        else:assert not (tmp_path/'delivery.txt').exists() and not result.verification
    asyncio.run(run())


def test_completion_metadata_reports_effective_repair_cap_not_raw_request(tmp_path):
    from forge.sessions.store import SessionJournal
    from forge.observability.recorder import JournalRecorder
    from forge.runtime.runner import TurnRunner
    from tests.implementation.integration.test_traces import observations
    async def run():
        journal=SessionJournal(tmp_path/'events.jsonl',session_id='session-'+'a'*24,project_root=tmp_path)
        journal.append('session_started',{})
        recorder=JournalRecorder(journal)
        runner=TurnRunner(Conversation(client=object(),context_root=tmp_path,registry=create_default_registry(tmp_path),
            task_policy=TaskPolicy(max_delivery_repairs=10)))
        with recorder.turn():
            assert [runner._offer_delivery_repair((gap,)) for gap in ('first','second','third')]==[True,True,False]
            await runner._finish('partial','acceptance_unmet','Incomplete',('unmet',))
        terminal=next(e for e in reversed(observations(journal)) if e['event_type']=='completion.rejected')
        assert terminal['attributes']['repairs_remaining']==0
    asyncio.run(run())


@pytest.mark.parametrize('repairs', [0, 2, 10])
def test_adapter_exports_actual_repair_switch_and_keeps_existing_defaults(tmp_path, repairs):
    from forge.application.harness_adapter import HarnessAdapter, LocalTrustedBackend
    from forge.config import ForgeConfig
    from benchmark.harbor.run_forge import BENCHMARK_TASK_POLICY
    class Client:
        provider = 'scripted'
        async def aclose(self): pass
    policy = TaskPolicy(max_delivery_repairs=repairs)
    adapter = HarnessAdapter(tmp_path, config=ForgeConfig(api_key='offline-fixture', model_id='scripted-test'),
        data_root=tmp_path/'private-data', backend=LocalTrustedBackend(),
        budget={'max_model_calls': 5, 'max_tool_calls': 2, 'wall_seconds': 30},
        model_client_factory=lambda *args, **kwargs: Client(), task_policy=policy)
    try:
        assert adapter.conversation.completion_gate.policy == policy
        capability = adapter.capabilities['delivery_repair']
        assert capability == {'parameter': 'TaskPolicy.max_delivery_repairs',
            'configuration_key': 'max_delivery_repairs', 'default': 0, 'maximum': 2,
            'configured': repairs, 'effective': min(2, repairs),
            'distinct_gap_sets_only': True, 'budget': 'shared_with_parent_attempt', 'supported': True}
        assert TaskPolicy().max_delivery_repairs == 0
        assert BENCHMARK_TASK_POLICY.max_delivery_repairs == 2
    finally:
        asyncio.run(adapter.close())


def test_external_edit_expires_actual_verify_and_completion_event(tmp_path):
    from forge.sessions.store import SessionJournal
    from tests.implementation.integration.test_traces import observations
    project = tmp_path / 'project'
    project.mkdir()
    target = project / 'delivery.txt'
    target.write_text('verified', encoding='utf-8')
    journal = SessionJournal(tmp_path/'private/events.jsonl', session_id='session-'+'b'*24, project_root=project)
    journal.append('session_started', {})
    class Client:
        provider = 'scripted'
        calls = 0
        async def stream(self, **kwargs):
            self.calls += 1
            yield ModelUsageUpdate(TokenUsage(1, 1), usage_is_final=True)
            if self.calls == 1:
                yield ModelToolCallCompleted(ToolCall(0, 'verify', 'verify', {
                    'command': '"'+sys.executable+'" -',
                    'stdin': "from pathlib import Path\nassert Path('delivery.txt').read_text()=='verified'\nprint('checked')\n"}))
            else:
                target.write_text('changed externally', encoding='utf-8')
                yield ModelTextDelta('Delivery complete.')
    async def approve(request):
        return ApprovalResponse('allow_once', 'Controlled offline verification')
    async def run():
        client = Client()
        conversation = Conversation(client=client, context_root=project, registry=create_default_registry(project),
            max_iterations=2, session_journal=journal, task_policy=TaskPolicy(require_verification=True),
            permission_manager=PermissionManager(project, mode='supervised', approval_handler=approve, load_stored_rules=False))
        async for event in conversation.stream('Verify delivery.txt before completing.'):
            if isinstance(event, TurnCompleted): result = event.result
        assert result.status == 'partial' and result.verification.success
        assert result.verification.workspace_revision < conversation.turn_state.workspace_revision
        assert result.completion_report.verification_status == 'unverified'
        assert result.completion_report.historical_checks
        events = observations(journal)
        assert any(e['event_type'] == 'verification.invalidated' for e in events)
        terminal = next(e for e in reversed(events) if e['event_type'] == 'completion.rejected')
        assert terminal['attributes']['completion_report']['verification_status'] == 'unverified'
        assert terminal['attributes']['completion_report']['historical_checks']
        assert client.calls == 2
    asyncio.run(run())
