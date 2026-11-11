"""Real SDK transports retry only before semantic output, with one delay owner."""
import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace

import httpx
import pytest
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from forge.config import ForgeConfig
from forge.runtime.gemini import GeminiModelClient
from forge.runtime.model_client import AnthropicModelClient
from forge.runtime.providers import NativeModelClient
from forge.runtime.retry import retry_delay
from forge.runtime.state import ModelCallError, ModelRetryScheduled


@pytest.mark.parametrize('headers, expected', [
    ({'retry-after': '3'}, 3), ({'retry-after-ms': '250', 'retry-after': '2'}, .25),
    ({'retry-after': '61'}, None), ({'retry-after': 'invalid'}, .5),
    ({'retry-after': 'nan'}, .5), ({'retry-after': 'inf'}, .5),
    ({'retry-after': '-1'}, .5), ({'retry-after': '0'}, .5),
])
def test_delay_respects_headers_without_shortening_server_wait(monkeypatch, headers, expected):
    monkeypatch.setattr('forge.runtime.retry.random.uniform', lambda *_: 0)
    assert retry_delay(1, SimpleNamespace(response=httpx.Response(429, headers=headers))) == expected


def test_retry_after_http_date():
    value = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30), usegmt=True)
    delay = retry_delay(1, SimpleNamespace(response=httpx.Response(503, headers={'retry-after': value})))
    assert 28 < delay <= 30


def client_for(provider, http):
    config = ForgeConfig(api_key='offline', model_id='offline', provider=provider,
                         base_url='https://offline.invalid')
    if provider == 'anthropic':
        return AnthropicModelClient.from_config(config, client=AsyncAnthropic(api_key='offline', http_client=http))
    if provider == 'gemini':
        return GeminiModelClient(config, client=http)
    return NativeModelClient(config, client=AsyncOpenAI(api_key='offline', http_client=http))


@pytest.mark.parametrize('provider', ['anthropic', 'deepseek', 'openai_responses', 'gemini'])
@pytest.mark.parametrize('status, header, attempts', [(429, '2', 3), (503, '61', 1), (401, '2', 1)])
def test_retry_uses_real_http_headers_and_does_not_stack_sdk_retries(monkeypatch, provider, status, header, attempts):
    requests, delays, events = [], [], []
    def handler(request):
        requests.append(request)
        return httpx.Response(status, headers={'retry-after': header}, json={'error': {'message': 'busy'}})
    async def sleep(seconds):
        delays.append(seconds)
    monkeypatch.setattr(asyncio, 'sleep', sleep)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            adapter = client_for(provider, http)
            with pytest.raises(ModelCallError):
                async for event in adapter.stream([{'role': 'user', 'content': 'test'}]):
                    events.append(event)
    asyncio.run(run())
    assert len(requests) == attempts
    assert delays == [2] * (attempts - 1)
    assert [event.attempt for event in events if isinstance(event, ModelRetryScheduled)] == list(range(2, attempts + 1))


@pytest.mark.parametrize('provider', ['anthropic', 'deepseek', 'openai_responses', 'gemini'])
def test_cancellation_interrupts_real_backoff_without_another_request(provider):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(429, headers={'retry-after': '30'}, json={'error': {'message': 'busy'}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            adapter = client_for(provider, http)
            stream = adapter.stream([{'role': 'user', 'content': 'test'}])
            event = await anext(stream)
            assert isinstance(event, ModelRetryScheduled) and event.delay_seconds == 30
            waiting = asyncio.create_task(anext(stream))
            await asyncio.sleep(.01)
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(waiting, 1)
            await stream.aclose()
    asyncio.run(run())
    assert len(requests) == 1
