'''Ordinary coding receives check facts without an automatic rerun request.'''
import asyncio
from dataclasses import replace

import pytest

from forge.api import HarnessSession, SessionOptions
from forge.agents import AgentDefinition
from forge.config import ForgeConfig
from forge.runtime.state import ModelTextDelta, ModelResponseCompleted, ModelToolCallCompleted, ToolCall


def test_custom_tool_request_only_guides_available_actions(tmp_path):
    class Reader:
        model = 'offline'
        async def stream(self, messages, tools, system):
            assert [tool['name'] for tool in tools] == ['read_context_artifact']
            assert 'read_context_artifact' in messages[0]['content']
            for absent in ('finish_task', 'task_plan', 'review_delivery', 'job_kill', 'job_output'):
                assert absent not in system and absent not in str(messages)
            yield ModelTextDelta('done')
            yield ModelResponseCompleted('end_turn')
        async def aclose(self): pass

    async def run():
        root = tmp_path / 'workspace'
        root.mkdir()
        options = SessionOptions(root, data_root=tmp_path/'data', config=ForgeConfig(api_key='offline', model_id='offline'),
            agent=AgentDefinition(tools=('read_context_artifact',)))
        async with await HarnessSession.open(options, model_client_factory=lambda *a, **kw: Reader()) as session:
            assert (await session.run('Inspect this output.')).status == 'completed'
    asyncio.run(run())


def test_default_coding_tools_complete_a_partial_delivery(tmp_path, monkeypatch):
    import json
    import re
    monkeypatch.setenv('FORGECODE_DISABLE_GLOBAL_SKILLS', '1')
    root = tmp_path / 'project'
    root.mkdir()
    (root/'value.txt').write_text('old')
    class Model:
        model = 'offline'
        count = 0
        async def stream(self, messages, tools, system):
            schemas = {t['name']: t['input_schema'] for t in tools}
            assert {'find_files', 'read_file', 'replace_text', 'job_output', 'job_kill', 'verify'} <= schemas.keys()
            assert not {'git_status', 'review_delivery', 'explore_repository', 'task_plan'} & schemas.keys()
            assert 'acceptance_criteria' not in schemas['finish_task']['properties']
            calls = [('find_files', {'pattern': '*.txt'}), ('read_file', {'path': 'value.txt'}),
                ('replace_text', {'path': 'value.txt', 'old_text': 'old', 'new_text': 'new'}),
                ('verify', {'command': 'echo checked'}),
                ('run_command', {'command': 'echo background', 'run_in_background': True}),
                ('job_output', {'wait_for': 'completion', 'wait_seconds': 20}),
                ('finish_task', {'status': 'partial', 'task_kind': 'change', 'summary': 'One edit delivered.'})]
            name, args = calls[self.count]
            if name == 'job_output':
                match = re.search(r'job_id: ([\w-]+)', json.dumps(messages))
                assert match, 'Background receipt must identify the job'
                args['job_id'] = match[1]
            self.count += 1
            yield ModelToolCallCompleted(ToolCall(0, str(self.count), name, args))
            yield ModelResponseCompleted('tool_use')
        async def aclose(self): pass
    async def run():
        options = SessionOptions(root, data_root=tmp_path/'data', config=ForgeConfig(api_key='offline', model_id='offline'))
        async with await HarnessSession.open(options, model_client_factory=lambda *a, **kw: Model()) as session:
            result = await session.run('Change the value, check it and report incomplete delivery.')
            assert result.status == 'partial' and result.model_calls == 7
            assert (root/'value.txt').read_text() == 'new'
    asyncio.run(run())


@pytest.mark.parametrize('foreground_only', [False, True])
def test_request_matches_job_permissions_and_controls(tmp_path, foreground_only):
    root = tmp_path/'project'
    root.mkdir()
    class Model:
        model = 'offline'
        async def stream(self, messages, tools, system):
            schemas = {t['name']: t['input_schema'] for t in tools}
            if foreground_only:
                assert set(schemas) == {'run_command'}
                fields = schemas['run_command']['properties']
                assert 'lifetime' not in fields and 'run_in_background' not in fields
                assert fields['timeout_seconds']['type'] == 'number'
                assert fields['timeout_seconds']['default'] == 120
            else:
                assert 'job_output' in schemas
                assert not {'job_kill', 'run_command', 'verify', 'write_file'} & schemas.keys()
            yield ModelTextDelta('Checked')
            yield ModelResponseCompleted('end_turn')
        async def aclose(self): pass
    async def run():
        agent = AgentDefinition(tools=('run_command',)) if foreground_only else AgentDefinition()
        options = SessionOptions(root, data_root=tmp_path/'data', agent=agent,
            config=ForgeConfig(api_key='offline', model_id='offline'))
        async with await HarnessSession.open(options, model_client_factory=lambda *a, **kw: Model()) as session:
            if not foreground_only:
                session.conversation.permission_manager.set_mode('plan')
            assert (await session.run('Inspect available capabilities')).status == 'completed'
    asyncio.run(run())


@pytest.mark.parametrize('strict', [False, True])
def test_verification_guidance_preserves_the_selected_completion_policy(tmp_path, strict):
    root = tmp_path/'project'
    root.mkdir()
    class Model:
        model = 'offline'
        count = 0
        async def stream(self, messages, **kw):
            self.count += 1
            if self.count == 1:
                yield ModelToolCallCompleted(ToolCall(0,'check','verify',{'command':'echo checked',
                    **({'covers':['fixture output']} if strict else {})}))
                yield ModelResponseCompleted('tool_use')
            else:
                context = messages[-1]['content']
                assert '[Executed check state]' in context and 'observed_success' in context
                assert ('Use verify.check_id to rerun' in context) == strict
                assert ('freshness change alone is not a request to rerun' in context) != strict
                # A failed declaration permits the strict fixture to end without
                # pretending a trivial echo is positive behavioral verification.
                yield ModelToolCallCompleted(ToolCall(0,'finish','finish_task',{
                    'status':'failed' if strict else 'completed', 'task_kind':'answer', 'summary':'Fixture done.'}))
                yield ModelResponseCompleted('tool_use')
        async def aclose(self): pass
    async def run():
        model = Model()
        async with await HarnessSession.open(SessionOptions(root, data_root=tmp_path/'data',
            config=ForgeConfig(api_key='offline', model_id='offline'),
            agent=replace(AgentDefinition(), completion='strict' if strict else 'normal')),
            model_client_factory=lambda *a, **kw: model) as session:
            result = await session.run('Exercise the check guidance.')
            assert result.status == ('failed' if strict else 'completed') and model.count == 2
    asyncio.run(run())


def test_normal_check_context_keeps_distinct_stdin_programs(tmp_path):
    import sys
    root = tmp_path / 'project'
    root.mkdir()
    class Model:
        model = 'offline'
        count = 0
        async def stream(self, messages, **kwargs):
            self.count += 1
            if self.count <= 2:
                yield ModelToolCallCompleted(ToolCall(0, f'check-{self.count}', 'verify', {
                    'command': f'"{sys.executable}" -',
                    'stdin': 'raise SystemExit(1)' if self.count == 1 else 'print("ok")'}))
                yield ModelResponseCompleted('tool_use')
            else:
                context = messages[-1]['content']
                assert context.count('"observed_success"') == 2
                assert '"observed_success": false' in context and '"observed_success": true' in context
                yield ModelTextDelta('Both command results were recorded.')
                yield ModelResponseCompleted('end_turn')
        async def aclose(self): pass
    async def run():
        async with await HarnessSession.open(SessionOptions(root, data_root=tmp_path / 'data',
            config=ForgeConfig(api_key='offline', model_id='offline')),
            model_client_factory=lambda *a, **k: Model()) as session:
            result = await session.run('Run the two independent diagnostic programs.')
            assert result.status == 'completed' and result.model_calls == 3
    asyncio.run(run())
