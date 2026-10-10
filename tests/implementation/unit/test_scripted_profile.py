import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from forge.application.models import ContractError, strict_loads
from forge.config import ForgeConfig
from forge.engine.test_profile import load_scripted_profile, scripted_profile
from forge.permissions.risk import classify_tool_call
from forge.runtime.state import ModelTextDelta, ModelUsageUpdate, ToolCall


FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures' / 'fix-python-add' / 'script.json'


@pytest.mark.parametrize('invalid', [
    {'schema_version': 'unknown'}, {'origin': 'live'}, {'max_steps': 0}, {'max_steps': 33}, {'max_steps': True},
    {'max_steps': 1}, {'allowed_tools': ['explore_repository']}, {'allowed_tools': ['verify', 'verify']},
    {'credentials': []}, {'credentials': {'key': 1}}, {'approve_scripted_calls': 'yes'}, {'unexpected': True},
    {'responses': []}, {'responses': [{'usage': {'input_tokens': -1, 'output_tokens': 1}}]},
    {'responses': [{'usage': {'input_tokens': 1.5, 'output_tokens': 1}}]}, {'responses': [{'delay_seconds': 61}]},
    {'responses': [{'error': 'arbitrary_exception'}]}, {'responses': [{'unknown': True}]},
    {'responses': [{'tool_calls': [{'id': 'bad', 'name': 'run_command', 'arguments': {}}]}]},
])
def test_invalid_script_rejected_before_any_execution(invalid):
    value = strict_loads(FIXTURE.read_bytes())
    value.update(invalid)
    with pytest.raises(ContractError):
        scripted_profile(value)


def test_missing_version_and_duplicate_tool_ids_are_rejected():
    value = strict_loads(FIXTURE.read_bytes())
    legacy = {'credentials': {}, 'responses': value['responses']}
    with pytest.raises(ContractError):
        scripted_profile(legacy)
    value['responses'][1]['tool_calls'] = deepcopy(value['responses'][0]['tool_calls'])
    with pytest.raises(ContractError, match='duplicate'):
        scripted_profile(value)


def test_scripted_approval_does_not_grant_other_calls():
    profile = load_scripted_profile(FIXTURE)
    request = classify_tool_call(ToolCall(0, 'before', 'verify', {'command': '${PYTHON} -m unittest -v', 'cwd': '.',
        'covers': ['integer addition including negative inputs']}), 'process')
    assert asyncio.run(profile.approval_handler(request)).choice == 'allow_once'
    assert asyncio.run(profile.approval_handler(replace(request, preview='unlisted command'))).choice == 'deny'
    assert asyncio.run(profile.approval_handler(replace(request, hard_deny=True))).choice == 'deny'


def test_provider_interruption_does_not_invent_usage():
    path = FIXTURE.parents[1] / 'provider-interruption' / 'script.json'
    profile = load_scripted_profile(path)
    client = profile.model_client_factory(ForgeConfig(api_key='test', model_id='scripted'))
    observed = []

    async def run():
        with pytest.raises(ConnectionError):
            async for event in client.stream([]):
                observed.append(event)
        with pytest.raises(RuntimeError, match='exhausted'):
            async for _ in client.stream([]):
                pass

    asyncio.run(run())
    assert client.calls == 1
    assert any(isinstance(event, ModelTextDelta) for event in observed)
    assert not any(isinstance(event, ModelUsageUpdate) for event in observed)
