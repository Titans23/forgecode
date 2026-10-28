from dataclasses import replace

import pytest

from forge.config import ForgeConfig
from forge.runtime.model_client import ModelCallError
from forge.runtime.service_health import ServiceHealth, profile_health


def test_shared_model_contracts_keep_legacy_identity():
    from forge.runtime import model_client, state
    for name in ('ModelClient', 'ModelCallError', 'ModelProtocolError', 'ModelOutputTruncatedError'):
        assert getattr(model_client, name) is getattr(state, name)
    error=state.ModelOutputTruncatedError(('read_file',))
    assert isinstance(error, model_client.ModelProtocolError)
    assert error.reason=='output_truncated' and error.tool_name=='read_file'


def test_health_and_routing_do_not_load_provider_implementation():
    from pathlib import Path
    import subprocess
    import sys
    script='''
import builtins
original=builtins.__import__
def guarded(name,*args,**kwargs):
    if name.startswith(('anthropic','openai','forge.runtime.model_client','forge.runtime.providers')):
        raise AssertionError('Provider implementation imported: '+name)
    return original(name,*args,**kwargs)
builtins.__import__=guarded
from forge.runtime.state import ModelClient,ModelCallError
from forge.runtime.router import ModelIntentRouter
from forge.runtime.service_health import ServiceHealth
class Client:
    provider='offline'
    async def stream(self, messages, tools=None, system=None):
        yield None
assert isinstance(Client(),ModelClient)
ModelIntentRouter(Client())
health=ServiceHealth(threshold=1)
health.failed(now=0)
try:
    health.before_request(now=1)
except ModelCallError as error:
    assert error.reason=='service_circuit_open' and error.retryable is False
else:
    raise AssertionError('Open circuit allowed a request')
health.succeeded()
health.before_request(now=1)
'''
    result=subprocess.run([sys.executable,'-c',script],cwd=Path(__file__).resolve().parents[2],
        capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr


def test_circuit_allows_only_one_recovery_probe_then_pauses():
    health = ServiceHealth()
    for _ in range(3):
        health.before_request(now=0)
        health.failed(now=0)
    with pytest.raises(ModelCallError) as caught:
        health.before_request(now=59)
    assert caught.value.reason == 'service_circuit_open'
    health.before_request(now=61)
    with pytest.raises(ModelCallError):
        health.before_request(now=61)
    health.failed(now=61)
    health.probe_inflight = False
    with pytest.raises(ModelCallError) as caught:
        health.before_request(now=1000)
    assert caught.value.reason == 'service_paused'
    health.succeeded()  # Explicit recovery reset.
    health.before_request(now=1000)


def test_routing_clients_share_health_without_affecting_other_profiles():
    config = ForgeConfig(api_key='test', model_id='test')
    first = profile_health(config)
    assert profile_health(config) is first
    assert profile_health(replace(config, model_id='other')) is not first
    assert profile_health(replace(config, api_key='other')) is not first
