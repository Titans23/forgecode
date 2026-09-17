from dataclasses import replace

import pytest

from forge.config import ForgeConfig
from forge.runtime.model_client import ModelCallError
from forge.runtime.service_health import ServiceHealth, profile_health


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
