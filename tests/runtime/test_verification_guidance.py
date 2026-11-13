'''Ordinary coding receives check facts without an automatic rerun request.'''
import asyncio
from dataclasses import replace

import pytest

from forge.api import HarnessSession, SessionOptions
from forge.agents import AgentDefinition
from forge.config import ForgeConfig
from forge.runtime.state import ModelTextDelta, ModelResponseCompleted, ModelToolCallCompleted, ToolCall


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
                yield ModelToolCallCompleted(ToolCall(0,'check','verify',{'command':'echo checked', 'covers':['fixture output']}))
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
